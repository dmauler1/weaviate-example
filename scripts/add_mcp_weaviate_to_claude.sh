#!/bin/bash
claude mcp add-json "spring-boot-docs" \
  '{"command":"/home/coder/.pyenv/shims/uvx","args":["mcp-weaviate",
  "--connection-type","local","--host","localhost",
  "--port","8080","--grpc-port","50051"]}'