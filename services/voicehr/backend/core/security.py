import uuid

def generate_token() -> str:
    return str(uuid.uuid4())

def generate_report_token() -> str:
    return str(uuid.uuid4())
