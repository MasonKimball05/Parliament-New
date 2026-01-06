#!/bin/bash

# Seed Feature Flags and Page Toggles
# This script adds all feature flags and page toggles to the database

echo "========================================="
echo "  Seeding Feature Flags & Page Toggles  "
echo "========================================="
echo ""

# Run the management command
python3 manage.py seed_feature_flags

echo ""
echo "========================================="
echo "  ✅ Seeding Complete!                  "
echo "========================================="
