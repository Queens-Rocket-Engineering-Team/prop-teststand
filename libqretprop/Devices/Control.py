class Control:
    def __init__(self,
                 name: str,
                 controlType: str,
                 control_index: str,
                 defaultState: str) -> None:

        self.name = name
        self.controlType = controlType
        self.control_index = control_index
        self.defaultState = defaultState  # Default state
        self.state = defaultState
