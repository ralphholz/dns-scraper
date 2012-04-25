-- __SCHEMAPLACEHOLDER__ will be replaced by sed for actual schema name
DROP SCHEMA IF EXISTS __SCHEMAPLACEHOLDER__ CASCADE;
CREATE SCHEMA __SCHEMAPLACEHOLDER__;

SET search_path = __SCHEMAPLACEHOLDER__;

DROP TYPE  IF EXISTS validation_result;
CREATE TYPE validation_result AS ENUM ('insecure', 'secure', 'bogus');

--CREATE LANGUAGE plpgsql;

CREATE TABLE domains (
	id SERIAL PRIMARY KEY,
	fqdn VARCHAR(255) UNIQUE NOT NULL
);

CREATE FUNCTION insert_unique_domain(new_fqdn VARCHAR) RETURNS INTEGER AS
$$
DECLARE
	new_index INTEGER;
BEGIN
	SELECT domains.id FROM domains WHERE fqdn = new_fqdn LIMIT 1 INTO new_index;
        IF new_index IS NOT NULL THEN
		RETURN new_index;
        ELSE
                INSERT INTO domains (fqdn) VALUES (new_fqdn) RETURNING id INTO new_index;
		RETURN new_index;
        END IF;
END;
$$ LANGUAGE plpgsql;

-- Table for RRSIGs
CREATE TABLE rrsig_rr (
    id SERIAL PRIMARY KEY,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    rr_type INTEGER NOT NULL,
    algo SMALLINT NOT NULL,
    labels SMALLINT NOT NULL,
    orig_ttl INTEGER NOT NULL,
    sig_expiration TIMESTAMP WITH TIME ZONE NOT NULL,
    sig_inception TIMESTAMP WITH TIME ZONE NOT NULL,
    keytag INTEGER NOT NULL,
    signer VARCHAR(255) NOT NULL,
    signature BYTEA NOT NULL
);

--CREATE INDEX rrsig_rr_fqdn_id_type_idx ON rrsig_rr (fqdn_id, rr_type);

-- Table for A and AAAA records
CREATE TABLE aa_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    addr INET NOT NULL
);

--CREATE INDEX aa_rr_fqdn_id_idx ON aa_rr (fqdn_id);

-- Table for DNSKEY
CREATE TABLE dnskey_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    flags INTEGER NOT NULL,
    protocol SMALLINT NOT NULL,
    algo SMALLINT NOT NULL,
    rsa_exp BIGINT, -- bigger exponents will have -1 here and pubkey will be unparsed in other_key field
    rsa_mod BYTEA, -- RSA exponent without leading zeros if exponent fits in rsa_exp
    other_key BYTEA -- all other non-RSA keys unparsed (including RSA keys with too large exponent)
);

--CREATE INDEX dnskey_rr_fqdn_id_algo_idx ON dnskey_rr (fqdn_id, algo);

-- Table for NSEC records
CREATE TABLE nsec_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    rr_type INTEGER NOT NULL, -- RR type that was used in question
    owner VARCHAR(255) NOT NULL,
    ttl INTEGER NOT NULL,
    rcode SMALLINT NOT NULL,
    next_domain VARCHAR(255) NOT NULL,
    type_bitmap INTEGER[] NOT NULL
);

--CREATE INDEX nsec_rr_fqdn_id_idx ON nsec_rr (fqdn_id, rr_type);

-- Table for NSEC3 records
CREATE TABLE nsec3_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    rr_type INTEGER NOT NULL, -- RR type that was used in question
    owner VARCHAR(255) NOT NULL,
    ttl INTEGER NOT NULL,
    rcode SMALLINT NOT NULL,
    hash_algo SMALLINT NOT NULL,
    flags SMALLINT NOT NULL,
    iterations INTEGER NOT NULL,
    salt BYTEA NOT NULL,
    next_owner VARCHAR(255) NOT NULL,
    type_bitmap INTEGER[] NOT NULL
);

--CREATE INDEX nsec3_rr_fqdn_id_idx ON nsec3_rr (fqdn_id, rr_type);

-- Table for NS records
CREATE TABLE ns_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    nameserver VARCHAR(255) NOT NULL
);

--CREATE INDEX ns_rr_fqdn_id_idx ON ns_rr (fqdn_id);

-- Table for DS records
CREATE TABLE ds_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    keytag INTEGER NOT NULL,
    algo SMALLINT NOT NULL,
    digest_type SMALLINT NOT NULL,
    digest BYTEA NOT NULL
);

--CREATE INDEX ds_rr_fqdn_id_idx ON ds_rr (fqdn_id);

-- Table for SOA records
CREATE TABLE soa_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    authority BOOLEAN NOT NULL, -- if true, it's from authority section, otherwise from answer section
    ttl INTEGER NOT NULL,
    zone VARCHAR(255), -- dname in case of storing from authority section
    mname VARCHAR(255) NOT NULL,
    rname VARCHAR(255) NOT NULL,
    serial BIGINT NOT NULL,
    refresh INTEGER NOT NULL,
    retry INTEGER NOT NULL,
    expire INTEGER NOT NULL,
    minimum INTEGER NOT NULL
);

--CREATE INDEX soa_rr_fqdn_id_idx ON soa_rr (fqdn_id);

-- Table for SSHFP records
CREATE TABLE sshfp_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    algo SMALLINT NOT NULL,
    fp_type SMALLINT NOT NULL,
    fingerprint BYTEA NOT NULL
);

--CREATE INDEX sshfp_rr_fqdn_id_idx ON sshfp_rr (fqdn_id);

-- Table for TXT records
CREATE TABLE txt_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    value BYTEA NOT NULL
);

--CREATE INDEX txt_rr_fqdn_id_idx ON txt_rr (fqdn_id);

-- Table for SPF records
CREATE TABLE spf_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    value BYTEA NOT NULL
);

--CREATE INDEX spf_rr_fqdn_id_idx ON spf_rr (fqdn_id);

-- Table for NSEC3PARAM records
CREATE TABLE nsec3param_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    hash_algo SMALLINT NOT NULL,
    flags SMALLINT NOT NULL,
    iterations INTEGER NOT NULL,
    salt BYTEA NOT NULL
);

--CREATE INDEX nsec3param_rr_fqdn_id_idx ON nsec3param_rr (fqdn_id);

-- Table for MX records
CREATE TABLE mx_rr (
    id SERIAL PRIMARY KEY,
    secure validation_result,
    fqdn_id INTEGER REFERENCES domains(id),
    ttl INTEGER NOT NULL,
    preference INTEGER NOT NULL,
    exchange VARCHAR(255) NOT NULL
);

--CREATE INDEX mx_rr_fqdn_id_idx ON mx_rr (fqdn_id);



