#!/usr/bin/env python

"""
Looks at each RSA key and prints out all keys that either have small moduli < 1023
bits or small exponents (where a validating resolver implementation might be
susceptible to variants of Bleichenbacher attack).

Reports also keys with big exponents (which slow down verification at resolvers).
"""

import sys

from ConfigParser import SafeConfigParser

from db import DbPool
from dns_scraper import DnskeyAlgo

#minimal modulus size and exponent size that is considered "safe"
b1023 = 1<<1023
min_exponent = 65537
big_exponent = 0x100000000

rsaAlgoStr = ",".join([str(algo) for algo in DnskeyAlgo.rsaAlgoIds])

sqlRowCount = 2000


if __name__ == '__main__':
	if len(sys.argv) != 2: 
		print >> sys.stderr, "ERROR: usage: <scraper_config>" 
		sys.exit(1)
		
	scraperConfig = SafeConfigParser()
	scraperConfig.read(sys.argv[1])
	
	db = DbPool(scraperConfig, max_connections=1)
	
	# prefix/schema to use in DB:
	prefix = ""
	if scraperConfig.has_option("database", "prefix"):
		prefix = scraperConfig.get("database", "prefix")
	
	#We are using single thread, so let's set the schema using 'set search_path'
	if prefix:
		if not prefix.endswith("."):
			raise ValueError("Sorry, only schemes supported in this script")
		
		sql = "SET search_path = %s"
		sql_data = (prefix[:-1],)
		
		cursor = db.cursor()
		cursor.execute(sql, sql_data)
	
	#named cursor in order to not swap ourselves from the known universe
	cursor = db.cursor(name="dnskeys")
	
	sql = """SELECT dnskey_rr.id AS id, fqdn, rsa_exp, encode(rsa_mod, 'hex') AS rsa_mod_hex
			FROM dnskey_rr INNER JOIN domains ON (fqdn_id=domains.id)
			WHERE algo IN (%s)
		""" % rsaAlgoStr
	
	cursor.execute(sql)
	rows = cursor.fetchmany(sqlRowCount)
	
	while rows:
		for row in rows:
			rowId = row["id"]
			fqdn = row["fqdn"]
			rsa_exp = row["rsa_exp"]
			rsa_mod_hex = row["rsa_mod_hex"]
			rsa_mod = int(rsa_mod_hex, 16)
			
			if rsa_mod < b1023:
				print "Small modulus: id %s, fqdn %s, mod 0x" % (rowId, fqdn, rsa_mod_hex)
			
			if rsa_exp == -1: #special value for exponent that won't fit into int64_t
				print "HUGE exponent: id %s, fqdn %s" % (rowId, fqdn)
			elif rsa_exp < min_exponent:
				print "Small exponent %s: id %s, fqdn %s" % (rsa_exp, rowId, fqdn)
			elif rsa_exp > big_exponent:
				print "Big exponent 0x%x: id %s, fqdn %s" % (rsa_exp, rowId, fqdn)
			
		rows = cursor.fetchmany(sqlRowCount)
		
		
