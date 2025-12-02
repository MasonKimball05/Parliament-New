def is_chair_or_officer(user):
    return user.member_type in ['Chair', 'Officer']