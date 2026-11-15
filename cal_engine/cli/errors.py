# CLI-specific error handling

class CLIError(ValueError):
    """The command line is valid shell syntax but invalid cal usage."""