cd ~/Database_Project/mariadb
cat > Dockerfile << 'EOF'
FROM mariadb:latest

ENV MYSQL_ROOT_PASSWORD=mariadb123

COPY schema.sql /docker-entrypoint-initdb.d/01-schema.sql

EXPOSE 3306
EOF