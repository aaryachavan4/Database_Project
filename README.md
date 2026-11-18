## Platforms

Tested on macOS with Docker Desktop and on WSL2 Ubuntu with Docker Desktop. Run all commands in a bash-compatible shell. Native Linux has not been tested.

A complete WSL2 Ubuntu run was verified on November 18, 2026. The workflow successfully:

- connected to an unsealed HashiCorp Vault instance
- created a MariaDB Vault token
- built the MariaDB Docker image
- loaded 10,000 customers
- loaded 50,000 accounts
- loaded 1,000,000 transactions
- confirmed that the HashiCorp key management plugin was active
- confirmed that all three tablespaces were encrypted
- completed the connection test successfully

The successful run ended with:

```text
Done. MariaDB is running with encryption on.
```

`start.sh` adds:

```text
--add-host=host.docker.internal:host-gateway
```

which allows the MariaDB container to reach Vault through the host.

Each person ran the tests on their own Vault, so key-version numbers from separate test environments do not need to match.

---

## Key Findings

- **Encryption works:** the encrypted table files contain none of the tested real values, while an unencrypted copy of the same data exposes them. The encrypted files had 0 of 114 values readable, against 77 of 77 in the unencrypted copy (Part 4).
- **Rotation under load is safe in the tested scenario:** rotating the key while reads and writes were running caused 0 failed queries, and the data checksums were identical before and after (Part 4).
- **One rotation is not enough:** with `innodb_encryption_rotate_key_age = 1`, existing data remains on the older key until another key-version update makes it old enough to rotate (Parts 1 and 4).
- **Threads plus a Vault refusal can crash MariaDB:** if Vault refuses access while encryption threads are running, MariaDB can crash (Part 4).
- **Vault outages are survivable, with limits:** cached keys can keep normal reads and writes working while Vault is unavailable, but MariaDB cannot start without access to the required Vault key and active rotation can require Vault access (Part 4).

---

## Team and Method

**Team:**

- **Zoya:** MariaDB setup, schema, Docker image, rotation under load and the README quick start.
- **Durgesh:** HashiCorp Vault setup, policy, key creation, MariaDB/Vault integration and connection testing.
- **Lilian:** Performance benchmark (Part 3).
- **Aarya:** Security tests: file inspection, key rotation under load and Vault failure scenarios.

**How the work was done:**

- The project was tested using Docker Desktop on macOS and WSL2 Ubuntu.
- MariaDB and Vault run separately, and MariaDB retrieves its encryption key from Vault through the HashiCorp key-management plugin.
- The data is generated with Faker: 10,000 customers, 50,000 accounts and 1,000,000 transactions.
- Rotation was measured with `continuous_writes.py` and `security/key_rotation_test.py`, with reads and writes running during the tests.
- Vault failure scenarios were tested using `security/vault_failure_test.py`.

---

## Part 2: Vault Integration (Person 2: Durgesh)

HashiCorp Vault is used as the external key management system for MariaDB data-at-rest encryption.

The MariaDB `hashicorp_key_management` plugin connects to a Vault KV v2 secrets engine mounted at:

```text
mariadb/
```

The database encryption key is stored under key ID `1` at:

```text
mariadb/1
```

MariaDB receives its Vault authentication token at runtime through the `VAULT_TOKEN` environment variable.

Vault root tokens, MariaDB Vault tokens, unseal keys, database passwords, and encryption key values are not stored in the repository.

### Vault Policy

MariaDB uses:

```text
mariadb-policy
```

The policy grants only the permissions required by the MariaDB plugin:

```hcl
path "mariadb/data/*" {
  capabilities = ["read"]
}

path "sys/mounts/mariadb/tune" {
  capabilities = ["read"]
}
```

MariaDB therefore has read-only access to its encryption key and cannot create, update, or delete Vault keys.

### Vault Setup

The setup script:

```text
vault/setup.sh
```

performs the Vault-side configuration.

It:

- verifies that Vault is available
- creates the `mariadb/` KV v2 secrets engine if necessary
- creates or updates `mariadb-policy`
- creates encryption key ID `1` only if it does not already exist
- leaves an existing encryption key unchanged

The initial key is generated with:

```bash
openssl rand -hex 32
```

The unused Vault Transit engine is not part of the final integration.

### MariaDB Integration Verification

The HashiCorp integration was verified successfully.

Both MariaDB plugins were active:

```text
hashicorp_key_management        ACTIVE
hashicorp_key_management_cache  ACTIVE
```

The connection test confirmed:

```text
PASS innodb_encrypt_tables is ON or FORCE
PASS hashicorp plugin is active
PASS customers is encrypted
PASS accounts is encrypted
PASS transactions is encrypted
All checks passed
```

The connection-test script reads the MariaDB root password from the:

```text
MYSQL_ROOT_PASSWORD
```

environment variable rather than containing a hard-coded password.

### Key Rotation

A live Vault KV v2 key-rotation test was performed.

Before rotation:

```text
Vault key version:    3
MariaDB key version:  3
```

New key versions were then created in Vault.

After the rotation process, Vault reported:

```text
current_version = 5
```

The encrypted tablespaces for `customers`, `accounts`, and `transactions` subsequently reported:

```text
CURRENT_KEY_ID       1
CURRENT_KEY_VERSION  5
MIN_KEY_VERSION      3
```

During this test, MariaDB continued to serve the tested read and write operations without requiring a MariaDB restart.

A test customer row was inserted after rotation, successfully read back, and then deleted.

The project uses:

```text
innodb_encryption_rotate_key_age = 1
```

Testing showed that creating only one newer key version was not sufficient to move all existing encrypted data to the newer version. Two key-version updates were required before the tablespaces reported the newer current key version.

### Vault Failure Behaviour

When Vault was temporarily unavailable and the required key was already cached, MariaDB continued serving normal reads and writes.

However, later security testing showed an important limitation: if Vault becomes unavailable or refuses access while active encryption-rotation threads require the key manager, MariaDB can fail.

Caching therefore improves availability during a temporary Vault outage, but Vault still needs to be available during operations that require fresh key access.

Detailed Vault setup instructions, verification commands, rotation results and failure behaviour are documented in:

```text
vault/README.md
```

---

## Part 4: Security Testing (Person 4: Aarya)

This part checks whether the setup actually protects the data and how it behaves during key rotation and Vault failures.

The full methodology, results and recommendations are documented in:

```text
security/README.md
```
