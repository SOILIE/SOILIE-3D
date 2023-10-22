# -*- coding: utf-8 -*-
''' SOILIE 3D
    v.23.10.31
    Written by Mike Cichonski
    for the Science of Imagination Laboratory
    Carleton University

    File: progress_bar.py
    Provides a simple progress bar for any iterative operation.'''

def update (iteration, total, prefix = '', \
            suffix = '', decimals = 1, \
            length = 33, fill = '█'):
    '''initialize or update the current progress bar'''
    percent = ("{0:."+str(decimals)+"f}").format(100*(iteration/float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print ('\r%s |%s| %s%% %s' % (prefix, bar, percent, suffix),end='\r',)
    if iteration == total:
        print ('')
