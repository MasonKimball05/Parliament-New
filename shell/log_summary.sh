#!/bin/bash

KEYWORD=$1
LOG_FILE="logs/django_actions.log"

if [ -z "$KEYWORD" ]; then
  echo "Usage: ./log_summary.sh [keyword]"
  exit 1
fi

grep "$KEYWORD" "$LOG_FILE" | tail -n 30
