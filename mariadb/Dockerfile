FROM mariadb:latest

RUN apt-get update && \
    apt-get install -y mariadb-plugin-hashicorp-key-management && \
    rm -rf /var/lib/apt/lists/*
    
COPY hashicorp.cnf /etc/mysql/conf.d/hashicorp.cnf

EXPOSE 3306
