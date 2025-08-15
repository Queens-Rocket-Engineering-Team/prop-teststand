class Control:
    def __init__(self,
                 name: str,
                 controlType: str,
                 pin: int,
                 defaultState: str) -> None:

        self.name = name
        self.controlType = controlType
        self.pin = pin
        self.defaultState = defaultState  # Default state
        self.state = defaultState
