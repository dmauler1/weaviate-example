#!/bin/bash

source /home/coder/projects/weaviate/.venv/bin/activate
python ingest_springboot_docs.py \
  --site-path /home/coder/projects/weaviate/spring-boot/documentation/spring-boot-docs/build/site \
  --weaviate-url http://localhost:8080