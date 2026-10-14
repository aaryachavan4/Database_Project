FROM mariadb:latest

RUN apt-get update && \
    apt-get install -y mariadb-plugin-hashicorp-key-management && \
    rm -rf /var/lib/apt/lists/*

ENV MYSQL_ROOT_PASSWORD=root_password

EXPOSE 3306
