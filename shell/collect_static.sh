#!/bin/bash

# Script to collect static files for deployment

echo "Collecting static files..."

# Run Django collectstatic command
python3 ../manage.py collectstatic --noinput

echo "Static files collected successfully!"
