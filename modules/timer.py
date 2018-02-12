import time
## set up timer --------------------------------------------------##
def startTimer():
    return float(round(time.time() * 1000))
def endTimer(start):
    return (float(round(time.time()*1000)) - start)/1000
## -------------------------------------------------------------- ##

