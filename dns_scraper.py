#!/usr/bin/env python


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

"""
The unbound-1.4.16 patch in "patches" directory is required for this to
work. See README.md.
"""

import time
import sys
import threading
import Queue
import logging
import struct

from datetime import datetime
from binascii import hexlify
from ConfigParser import SafeConfigParser

import ldns

from db import DbPool
from unbound import ub_ctx, ub_version, ub_strerror, ub_ctx_config, \
	RR_CLASS_IN, RR_TYPE_DNSKEY, RR_TYPE_A, \
	RR_TYPE_AAAA, RR_TYPE_SSHFP, RR_TYPE_MX, RR_TYPE_DS, RR_TYPE_NSEC, \
	RR_TYPE_NSEC3, RR_TYPE_NSEC3PARAMS, RR_TYPE_RRSIG, RR_TYPE_SOA, \
	RR_TYPE_NS, RR_TYPE_TXT, RCODE_SERVFAIL

RR_TYPE_SPF = 99

class DnsError(RuntimeError):
	"""Exception for reporting internal unbound and ldns errors"""
	
	def __init__(self, reason):
		super(DnsError, self).__init__(reason)

class DnsConfigOptions(object):
	"""Container for parameters present in config file related to DNS
	resolution.
	"""
	
	def __init__(self, scraperConfig):
		"""Load options from configParser.
		@param scraperConfig: instance of RawConfigParser or subclass
		"""
		self.unboundConfig = None
		self.forwarder = None
		self.attempts = scraperConfig.getint("dns", "retries")
		
		if scraperConfig.has_option("dns", "unbound_config"):
			self.unboundConfig = scraperConfig.get("dns", "unboundConfig")
		if scraperConfig.has_option("dns", "forwarder"):
			self.forwarder = scraperConfig.get("dns", "forwarder")
			
			
		
def result2pkt(result):
	"""Extract ldns packet from ub_result.
	
	@raises DnsError: on malformed packet
	"""
	status, pkt = ldns.ldns_wire2pkt(result.packet)
	
	if status != 0:
		raise DnsError("Failed to parse DNS packet: %s" % ldns.ldns_get_errorstr_by_id(status))
	
	return pkt
	
def validationToDbEnum(result):
	"""Given ub_result, returns on of three strings usable for the "secure"
	field in DB (secure, insecure, bogus).
	"""
	if result.secure:
		return "secure"
	elif result.bogus:
		return "bogus"
	else:
		return "insecure"

def getLdnsBufferData(buf, l):
	"""Return data from ldns_buffer buf of size l as pythonic string."""
	buf.flip()
	s = ""
	for i in range(l):
		s += chr(buf.read_u8())
	return s
	
def getRdfData(rdf):
	"""Return RDF bytes as pythonic string from ldns_rdf."""
	l = rdf.size()
	buf = ldns.ldns_buffer(l)
	rdf.write_to_buffer_canonical(buf)
	
	return getLdnsBufferData(buf, l)
	
def getRrData(rr, section=ldns.LDNS_SECTION_ANSWER):
	"""Return RR bytes as pythonic string from ldns_rr."""
	l = rr.uncompressed_size()
	buf = ldns.ldns_buffer(l)
	rr.write_to_buffer_canonical(buf, section)
	
	return getLdnsBufferData(buf, l)

def rdfConvert(rdf, fmt, conv=lambda x: x):
	"""Unpack and convert data from ldns_rdf.
	
	@param rdf: ldns_rdf
	@param fmt: format for struct.unpack
	@param conv: conversion function to call on result of struct.unpack()
	"""
	return conv(struct.unpack(fmt, getRdfData(rdf))[0])
		

class StorageQueueClient(object):
	"""Client for storing data passing it through queue to StorageThread."""
	
	def __init__(self, dbQueue):
		"""Initialize with queue to DB.
		@param dbQueue: instance of Queue.Queue for passing (sql,
		sql_data) to StorageThread
		"""
		self.dbQueue = dbQueue
	
	def sqlExecute(self, sql, sql_data):
		"""Execute storage command later in StorageThread.
		@param sql: sql query with %s "placeholders"
		@param sql_data: data for placeholders
		"""
		self.dbQueue.put((sql, sql_data))


class DnsMetadata(StorageQueueClient):
	"""Represents DNS(SEC) metadata in answer: RRSIGs, NSEC and NSEC3 RRs.
	These are some things we need to parse from answer packet by ldns.
	"""
	
	def __init__(self, pkt, rrType, dbQueue):
		"""Fills self with parsed data from DNS answer.
		
		@param pkt: ldns_pkt DNS answer packet
		@param rrType: RR type to use for RR selection
		@param dbQueue: DB queue for passing to StorageThread
		"""
		self.pkt = pkt
		self.rrType = rrType
		
		StorageQueueClient.__init__(self, dbQueue)
		
	def rrsigs(self, section=ldns.LDNS_SECTION_ANSWER):
		"""Return RRSIGs from selected section"""
		rrsigs = self.pkt.rr_list_by_type(RR_TYPE_RRSIG, section)
		return rrsigs and [rrsigs.rr(i) for i in range(rrsigs.rr_count())] or []
		
	def rrsigsStore(self, domain, section=ldns.LDNS_SECTION_ANSWER):
		"""Store RRSIGs from from given section of this packet in DB.
		Stores each RRSIG in separate transaction.
		"""
		rrsigs = self.rrsigs(section)
		sql = """INSERT INTO rrsig_rr
			(domain, ttl, rr_type, algo, labels, orig_ttl,
			sig_expiration, sig_inception,
			keytag, signer, signature)
			VALUES (%s, %s, %s, %s, %s, %s,
				to_timestamp(%s), to_timestamp(%s),
				%s, %s, %s)
			"""
		
		for rr in rrsigs:
			try:
				ttl = rr.ttl()
				algo = rdfConvert(rr.rrsig_algorithm(), "B")
				labels = rdfConvert(rr.rrsig_labels(), "B")
				orig_ttl = rdfConvert(rr.rrsig_origttl(), "!I")
				sig_expiration = rdfConvert(rr.rrsig_expiration(), "!I")
				sig_inception = rdfConvert(rr.rrsig_inception(), "!I")
				keytag = rdfConvert(rr.rrsig_keytag(), "!H")
				signer = str(rr.rrsig_signame()).rstrip(".")
				signature = buffer(getRdfData(rr.rrsig_sig()))
				
				sql_data = (domain, ttl, self.rrType, algo, labels, orig_ttl, sig_expiration,
					    sig_inception, keytag, signer, signature)
				self.sqlExecute(sql, sql_data)
			except:
				logging.exception("Failed to store RRSIG %s" % rr)
		
	def nsecs(self):
		"""Return NSEC records from additional section"""
		nsecs  = self.pkt.rr_list_by_type(RR_TYPE_NSEC,  ldns.LDNS_SECTION_ADDITIONAL)
		return nsecs  and [nsecs.rr(i)  for i in range(nsecs.rr_count()) ] or []
		
	def nsec3s(self):
		"""Return NSEC3 records from additional section"""
		nsec3s = self.pkt.rr_list_by_type(RR_TYPE_NSEC3, ldns.LDNS_SECTION_ADDITIONAL)
		return nsec3s and [nsec3s.rr(i) for i in range(nsec3s.rr_count())] or []
		

class RRTypeParser(StorageQueueClient):
	"""Abstract class for parsing and storing of all RR types."""
	
	rrType = 0 #undefined RR type
	rrClass = RR_CLASS_IN
	
	def __init__(self, domain, resolver, opts, dbQueue):
		"""Create instance.
		@param domain: domain to scan for
		@param resolver: ub_ctx to use for resolving
		@param opts: instance of DnsConfigOptions
		@param dbQueue: DB queue for passing to StorageThread
		"""
		self.domain = domain
		self.resolver = resolver
		self.opts = opts
		
		StorageQueueClient.__init__(self, dbQueue)
	
	def fetch(self):
		"""Generic fetching of record that are not of special form like
		SRV and TLSA.
		
		@returns: result part from (status, result) tupe of ub_ctx.resolve() or None on permanent SERVFAIL
		@throws: DnsError if unbound reports error
		"""
		for i in range(self.opts.attempts):
			(status, result) = self.resolver.resolve(self.domain, self.rrType, self.rrClass)
			
			if status != 0:
				raise DnsError("Resolving %s for %s: %s" % \
					(self.__class__.__name__, self.domain, ub_strerror(status)))
			
			if result.rcode != RCODE_SERVFAIL:
				logging.debug("Domain %s type %s: havedata %s, rcode %s", \
					self.domain, self.__class__.__name__, result.havedata, result.rcode_str)
				return result
			
		logging.warn("Permanent SERVFAIL: domain %s type %s", \
			self.domain, self.__class__.__name__)
		return None
		
	def fetchAndStore(self):
		"""Scan records for the domain and store results in DB.
		@return: number of records of given RR type found
		"""
		raise NotImplementedError
	

class AParser(RRTypeParser):
	
	rrType = RR_TYPE_A
	
	def __init__(self, domain, resolver, opts, dbQueue):
		RRTypeParser.__init__(self, domain, resolver, opts, dbQueue)
	
	def fetchAndStore(self):
		try:
			r = RRTypeParser.fetch(self)
			if not r:
				return 0
		except DnsError:
			logging.exception("Fetching of %s failed" % self.domain)
			return 0
		
		if r.havedata:
			secure = validationToDbEnum(r)
			pkt = result2pkt(r)
			
			rrs = pkt.rr_list_by_type(self.rrType, ldns.LDNS_SECTION_ANSWER)
			
			sql = """INSERT INTO aa_rr (secure, domain, ttl, addr)
				VALUES (%s, %s, %s, %s)
				"""
			for i in range(rrs.rr_count()):
				try:
					rr = rrs.rr(i)
					addr = str(rr.a_address())
					ttl = rr.ttl()
					
					sql_data = (secure, self.domain, ttl, addr)
					self.sqlExecute(sql, sql_data)
				except:
					logging.exception("Failed to parse %s %s" % (rr.get_type_str(), rr))
				
			meta = DnsMetadata(pkt, self.rrType, self.dbQueue)
			meta.rrsigsStore(self.domain)
			
			return rrs.rr_count()
		
		return 0

class AAAAParser(AParser):
	
	rrType = RR_TYPE_AAAA
	
	

class DnskeyAlgo:
	
	algoMap = \
		{ 
		     1: "RSA/MD5",
		     5: "RSA/SHA-1",
		     7: "RSASHA1-NSEC3-SHA1",
		     8: "RSA/SHA-256",
		    10: "RSA/SHA-512",
		}
	
	rsaAlgoIds = algoMap.keys()

class DNSKEYParser(RRTypeParser):

	rrType = RR_TYPE_DNSKEY
	maxDbExp = 9223372036854775807 #maximum exponent that fits in dnskey_rr.rsa_exp field
	
	def __init__(self, domain, resolver, opts, dbQueue):
		RRTypeParser.__init__(self, domain, resolver, opts, dbQueue)
	
	def fetchAndStore(self):
		try:
			result = RRTypeParser.fetch(self)
			if not result:
				return 0
		except DnsError:
			logging.exception("Fetching of %s failed" % self.domain)
			return 0
		
		secure = validationToDbEnum(result)
		if result.havedata:
			pkt = result2pkt(result)
			rrs = pkt.rr_list_by_type(self.rrType, ldns.LDNS_SECTION_ANSWER)
			
			sql = """INSERT INTO dnskey_rr
					(secure, domain, ttl, flags, protocol, algo, rsa_exp, rsa_mod, other_key)
					VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)
				"""
			
			for i in range(rrs.rr_count()):
				try:
					rr = rrs.rr(i)
					ttl = rr.ttl()
					flags = rdfConvert(rr.rdf(0), "!H")
					proto = rdfConvert(rr.rdf(1), "B")
					algo = rdfConvert(rr.rdf(2), "B")
					pubkey = getRdfData(rr.rdf(3))
					
					exponent = None
					modulus = None
					other_key = None
					
					if algo in DnskeyAlgo.rsaAlgoIds: #we have RSA key
						
						#RFC 2537/3110 exponent length encoding
						exp_len0 = ord(pubkey[0])
						if exp_len0 > 0:
							exp_len = exp_len0
							exp_hdr_len = 1
						else:
							exp_len = ord(pubkey[1]) << 8 + ord(pubkey[2])
							exp_hdr_len = 3
		
						exponent = int(hexlify(pubkey[exp_hdr_len:exp_hdr_len + exp_len]), 16)
						if exponent > self.maxDbExp: #needs to fit into DB field
							other_key = buffer(pubkey)
							exponent = -1
						else:
							modulus  = buffer(pubkey[exp_hdr_len + exp_len:].lstrip('\0'))
						
					else: #not a RSA key
						other_key = buffer(pubkey)
					
					sql_data = (secure, self.domain, ttl, flags, proto, algo, exponent, modulus, other_key)
					self.sqlExecute(sql, sql_data)
				except:
					logging.exception("Failed to store DNSKEY RR %s", rr)
			
			meta = DnsMetadata(pkt, self.rrType, self.dbQueue)
			meta.rrsigsStore(self.domain)
			
			return rrs.rr_count()
			
		return 0
			
	

class StorageThread(threading.Thread):
	"""Thread taking sql/sql_data from queue and executing it for storage in DB"""

	def __init__(self, db, dbQueue):
		"""Create storage thread.
		
		@param db: database connection pool, instance of db.DbPool
		@param dbQueue: instance of Queue.Queue that stores (sql,
		sql_data) tuples to be executed
		"""
		self.db = db
		self.dbQueue = dbQueue
		
		threading.Thread.__init__(self)

	def run(self):
		conn = self.db.connection()
		while True:
			sqlTuple = self.dbQueue.get()
			
			try:
				cursor = conn.cursor()
				sql, sql_data = sqlTuple
				cursor.execute(sql, sql_data)
			except Exception:
				logging.exception("Failed to execute `%s` with `%s`",
					sql, sql_data)
			finally:
				conn.commit()
				
			self.dbQueue.task_done()

class DnsScanThread(threading.Thread):

	def __init__(self, taskQueue, taFile, rrScanners, dbQueue, opts):
		"""Create scanning thread.
		
		@param taskQueue: Queue.Queue containing domains to scan as strings
		@param taFile: trust anchor file for libunbound
		@param rrScanners: list of subclasses of RRTypeParser to use for scan
		@param dbQueue: Queue.Queue for passing things to store to StorageThread
		@param opts: instance of DnsConfigOptions
		"""
		self.taskQueue = taskQueue
		self.rrScanners = rrScanners
		self.dbQueue = dbQueue
		self.opts = opts
		
		threading.Thread.__init__(self)
		
		self.resolver = ub_ctx()
		if opts.forwarder:
			self.resolver.set_fwd(opts.forwarder)
		self.resolver.add_ta_file(taFile) #read public keys for DNSSEC verification

	def run(self):
		while True:
			domain = self.taskQueue.get()
			
			for parserClass in self.rrScanners:
				try:
					parser = parserClass(domain, self.resolver, self.opts, self.dbQueue)
					parser.fetchAndStore()
				except Exception:
					logging.exception("Failed to scan domain %s with %s",
						domain, parserClass.__name__)
				
			self.taskQueue.task_done()


def convertLoglevel(levelString):
	"""Converts string 'debug', 'info', etc. into corresponding
	logging.XXX value which is returned.
	
	@raises ValueError if the level is undefined
	"""
	try:
		return getattr(logging, levelString.upper())
	except AttributeError:
		raise ValueError("No such loglevel - %s" % levelString)


if __name__ == '__main__':
	if len(sys.argv) != 3: 
		print >> sys.stderr, "ERROR: usage: <domain_file> <scraper_config>" 
		sys.exit(1)
		
	domainFilename = sys.argv[1]
	domainFile = file(domainFilename)
	scraperConfig = SafeConfigParser()
	scraperConfig.read(sys.argv[2])
	
	threadCount = scraperConfig.getint("processing", "scan_threads")
	
	#DNS resolution options
	taFile = scraperConfig.get("dns", "ta_file")
	opts = DnsConfigOptions(scraperConfig)
	if opts.unboundConfig:
		ub_ctx_config(opts.unboundConfig)
	
	#one DB connection per storage thread
	storageThreads = scraperConfig.getint("processing", "storage_threads")
	db = DbPool(scraperConfig, max_connections=storageThreads)
	
	logfile = scraperConfig.get("log", "logfile")
	loglevel = convertLoglevel(scraperConfig.get("log", "loglevel"))
	if logfile == "-":
		logging.basicConfig(stream=sys.stderr, level=loglevel,
			format="%(asctime)s %(levelname)s %(message)s [%(pathname)s:%(lineno)d]")
	else:
		logging.basicConfig(filename=logfile, level=loglevel,
			format="%(asctime)s %(levelname)s %(message)s [%(pathname)s:%(lineno)d]")
	
	#logging.info("Unbound version: %s", ub_version())
	logging.info("Starting scan of domains in file %s using %d threads.", domainFilename, threadCount)
	
	taskQueue = Queue.Queue(5000)
	dbQueue = Queue.Queue(500)
	
	parsers = [AParser, AAAAParser, DNSKEYParser]
	
	for i in range(threadCount):
		t = DnsScanThread(taskQueue, taFile, parsers, dbQueue, opts)
		t.setDaemon(True)
		t.start()
	
	for i in range(storageThreads):
		t = StorageThread(db, dbQueue)
		t.setDaemon(True)
		t.start()
	
	startTime = time.time()
	domainCount = 0
	
	for line in domainFile:
		domain = line.rstrip()
		taskQueue.put(domain)
		domainCount += 1
		
	taskQueue.join()
	
	logging.info("Waiting for storage threads to finish")
	dbQueue.join()
	
	logging.info("Fetch of dnskeys for %d domains took %.2f seconds", domainCount, time.time() - startTime)
