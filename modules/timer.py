import time
## set up timer --------------------------------------------------##
def startTimer():
    return float(round(time.time() * 1000))
def endTimer(start):
    return (float(round(time.time()*1000)) - start)/1000
## -------------------------------------------------------------- ##

def convert_seconds(seconds):
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return int(hours), int(minutes), int(seconds)

def endTimerPretty(start):
    seconds = endTimer(start)
    hours, minutes, remaining_seconds = convert_seconds(seconds)
    hours = '0'*(2-len(str(hours)))+str(hours)
    minutes = '0'*(2-len(str(minutes)))+str(minutes)
    remaining_seconds = '0'*(2-len(str(remaining_seconds)))+str(remaining_seconds)
    return f"{hours}h:{minutes}m:{remaining_seconds}s"
