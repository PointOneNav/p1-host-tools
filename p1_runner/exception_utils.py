
def exception_to_str(e: Exception) -> str:
    return f'{type(e).__name__}: "{str(e)}"'
