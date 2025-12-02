#!/bin/bash

# Script to apply database migrations

echo "Applying migrations..."

# Run Django migrations
python3 ../manage.py makemigrations

echo "A migrations has been successfully created!"
