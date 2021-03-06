#   This file is part of DNS Scraper
#
#   Copyright (C) 2012 Ondrej Mikle, CZ.NIC Labs
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, version 3 of the License.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.


from psycopg2.extras import DictCursor

#following ugliness is workaround for psycopg < 2.2.2 messing with logging system
try:
	import logging
	tmp = logging.basicConfig
	logging.basicConfig = lambda **kwargs: None
	from psycopg2.pool import PersistentConnectionPool
	logging.basicConfig = tmp
except:
	raise


class DbPool(object):
	"""DB class that makes connection transparently. Thread-safe - every
	thread get its own database connection.
	"""

	def __init__(self, config, min_connections=1, max_connections=5):
		"""Configures the Db, connection is not created yet.
		
		@param config: instance of RawConfigParser or subclass.
		@param min_connections: minimum connections in pool
		@param max_connections: maximum allowed connections in pool
		"""

		self.host = config.get("database", "host")
		self.port = config.getint("database", "port")
		self.user = config.get("database", "user")
		self.password = config.get("database", "password")
		self.db_name = config.get("database", "dbname")
		self.min_connections = min_connections
		self.max_connections = max_connections

		self.pool = PersistentConnectionPool(
			minconn = self.min_connections,
			maxconn = self.max_connections,
			host = self.host,
			port = self.port,
			user = self.user,
			password = self.password,
			database = self.db_name)

	def cursor(self, **kwargs):
		"""Creates and returns cursor for current thread's connection.
		Cursor is a "dict" cursor, so you can access the columns by
		names (not just indices), e.g.:

		cursor.execute("SELECT id, name FROM ... WHERE ...", sql_args)
		row = cursor.fetchone()
		id = row['id']
		
		Server-side cursors (named cursors) should be closed explicitly.
		
		@param kwargs: currently string parameter 'name' is supported.
		Named cursors are for server-side cursors, which
		are useful when fetching result of a large query via fetchmany()
		method. See http://initd.org/psycopg/docs/usage.html#server-side-cursors
		"""
		return self.connection().cursor(cursor_factory=DictCursor, **kwargs)
	
	def connection(self):
		"""Return connection for this thread"""
		return self.pool.getconn()

	def commit(self):
		"""Commit all the commands in this transaction in this thread's
		connection. If errors (e.g. duplicate key) arose, this will
		cause transaction rollback.
		"""
		self.connection().commit()

	def rollback(self):
		"""Rollback last transaction on this thread's connection"""
		self.connection().rollback()
	
	def putconn(self):
		"""Put back connection used by this thread. Necessary upon finishing of
		spawned threads, otherwise new threads won't get connection if the pool
		is depleted."""
		conn = self.connection()
		self.pool.putconn(conn)
	
	def close(self):
		"""Close connection."""
		self.connection().close()

class DbSingleThreadOverSchema(DbPool):
	"""Simple object mimicing the above DbPool. Should be used for data
	analysis - only single thread is needed, so 'SET search_path = bla'
	works.
	
	After initialization the caller just wants to ask for cursor() method.
	"""
	
	dbRows = 2000 #DB rows to fetch at once with named cursor via fetchmany()
	
	def __init__(self, config):
		"""Initialize DB for just one connection in single thread. Set
		schema based on config.
		
		@param config: ConfigParser instance
		@param cursorName: should be provided if huge data will be
		processed at small chunks, otherwise psycopg will load complete
		query result into memory
		
		@raises ValueError: if prefix from config does not end in dot,
		i.e. it must be a name of schema (which is trailed by dot in our
		case).
		
		Using this approach with 'SET search_path' is fine for single
		thread/connection, but wouldn't work for the multithreaded
		scanner."""
		
		super(DbSingleThreadOverSchema, self).__init__(config, min_connections=1, max_connections=1)
		
		self.prefix = ""
		if config.has_option("database", "prefix"):
			self.prefix = config.get("database", "prefix")
		
		#We are using single thread, so let's set the schema using 'set search_path'
		if self.prefix:
			if not self.prefix.endswith("."):
				raise ValueError("Sorry, only schemes supported in DBSingleThreadOverSchema")
			
			sql = "SET search_path = %s;"
			sql_data = (self.prefix[:-1],)
			
			cursor = self.cursor()
			cursor.execute(sql, sql_data)
			cursor.close()
	
	#rest of methods should be fine when inherited from parent
