#!/bin/sh
# Executado somente na criação do volume. Senhas lidas de /run/secrets (nunca do repositório).
# Usuários de runtime: apenas DML. Usuários *_migrate: DDL (Alembic). Sem consultas cruzadas entre bancos.
set -eu
r() { tr -d '\r\n' < "/run/secrets/$1"; }
mysql --protocol=socket -uroot -p"$(r mysql_root_password)" <<SQL
CREATE DATABASE IF NOT EXISTS ctrl_core CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS ctrl_fiscal CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS ctrl_nfse CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'core'@'%' IDENTIFIED BY '$(r core_db_password)';
CREATE USER IF NOT EXISTS 'core_migrate'@'%' IDENTIFIED BY '$(r core_migrate_db_password)';
CREATE USER IF NOT EXISTS 'fiscal'@'%' IDENTIFIED BY '$(r fiscal_db_password)';
CREATE USER IF NOT EXISTS 'fiscal_migrate'@'%' IDENTIFIED BY '$(r fiscal_migrate_db_password)';
CREATE USER IF NOT EXISTS 'nfse'@'%' IDENTIFIED BY '$(r nfse_db_password)';
CREATE USER IF NOT EXISTS 'nfse_migrate'@'%' IDENTIFIED BY '$(r nfse_migrate_db_password)';
GRANT SELECT, INSERT, UPDATE, DELETE ON ctrl_core.* TO 'core'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ctrl_fiscal.* TO 'fiscal'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ctrl_nfse.* TO 'nfse'@'%';
GRANT ALL PRIVILEGES ON ctrl_core.* TO 'core_migrate'@'%';
GRANT ALL PRIVILEGES ON ctrl_fiscal.* TO 'fiscal_migrate'@'%';
GRANT ALL PRIVILEGES ON ctrl_nfse.* TO 'nfse_migrate'@'%';
FLUSH PRIVILEGES;
SQL
