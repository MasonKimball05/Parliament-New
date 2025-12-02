# check_passed_legislation.py

from django.core.wsgi import get_wsgi_application
import os

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings_postgres')
application = get_wsgi_application()

from src.models import *

def verify_passed_legislation():
    errors = []
    for leg in Legislation.objects.filter(voting_closed=True):
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        abstain = votes.filter(vote_choice='abstain').count()

        total = yes + no
        threshold_met = (yes / total) >= (int(leg.required_percentage) / 100) if total > 0 else False

        if leg.vote_mode == 'percentage':
            print(f"[{leg.title}] - Yes: {yes}, No: {no}, Threshold: {leg.required_percentage}%, Passed: {leg.passed}, Legislation ID: {leg.id}")
        else:
            print(f'[{leg.title}], [Type: {leg.vote_mode}] - Passed: {leg.passed}, Legislation ID: {leg.id}')
            if leg.vote_mode == 'plurality':
                print(f"\t\t  Plurality options: {leg.plurality_options}\n")

        if (leg.passed != threshold_met) and leg.vote_mode == 'percentage':
            errors.append(f"❌ Mismatch on legislation ID {leg.id}: expected passed={threshold_met}, found {leg.passed}")

    if errors:
        print("\nDiscrepancies found:")
        for error in errors:
            print(error)
    else:
        print("\n✅ All passed legislation records are consistent!")

verify_passed_legislation()
