# BASE MICROPYTHON BOOT.PY-----------------------------------------------|  # noqa: INP001 # This is all micropython code to be executed on the esp32 system level
# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
#------------------------------------------------------------------------|


def setupRepl():
    global connectWifi, disconnectWifi
    from wifi_tools import connectWifi, disconnectWifi
