#!/bin/bash

echo "🧪 Dumping all model data from the database..."

python ../manage.py shell <<EOF
from django.apps import apps

for model in apps.get_models():
    model_name = model.__name__
    print(f"\\n📦 Model: {model_name}")
    objects = model.objects.all()
    if not objects:
        print("  (No records found)")
    for obj in objects:
        fields = vars(obj)
        for key, value in fields.items():
            if not key.startswith("_"):
                print(f"  {key}: {value}")
        print("-" * 30)
EOF