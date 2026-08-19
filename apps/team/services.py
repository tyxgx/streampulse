"""
Dummy team roster.

Edit the entries in get_team_members() with the real names, roles, and
profile links — each dict maps 1:1 onto a card on the Team page. `github`,
`linkedin`, and `email` default to "#" placeholders until real links are
available.
"""


def get_team_members():
    return [
        {'name': 'Team Member', 'initials': 'TM', 'role': 'Project Lead', 'github': '#', 'linkedin': '#', 'email': '#'},
        {'name': 'Team Member', 'initials': 'TM', 'role': 'Data Engineer — Ingestion', 'github': '#', 'linkedin': '#', 'email': '#'},
        {'name': 'Team Member', 'initials': 'TM', 'role': 'Data Engineer — Bronze/Silver', 'github': '#', 'linkedin': '#', 'email': '#'},
        {'name': 'Team Member', 'initials': 'TM', 'role': 'Data Engineer — Gold/Warehouse', 'github': '#', 'linkedin': '#', 'email': '#'},
        {'name': 'Team Member', 'initials': 'TM', 'role': 'Dashboard Developer', 'github': '#', 'linkedin': '#', 'email': '#'},
        {'name': 'Team Member', 'initials': 'TM', 'role': 'RAG / ML Engineer', 'github': '#', 'linkedin': '#', 'email': '#'},
    ]
