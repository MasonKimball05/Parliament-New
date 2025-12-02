#!/bin/bash

echo "⏳ Running vote consistency check..."
python3 check_passed_legislation.py || echo "❌ Failed to verify passed legislation."

echo "✅ Done."
