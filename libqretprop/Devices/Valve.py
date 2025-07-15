class Valve:
    def __init__(self,
                 name: str,
                 pin: int,
                 defaultState: str) -> None:

        self.name = name
        self.pin = pin
        self.defaultState = defaultState  # Default state
