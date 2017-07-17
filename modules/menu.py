''' VISUO 3D
    v.17.07.17
    Written by Mike Cichonski
    for the Science of Imagination Laboratory
    Carleton University

    File: menu.py
    This module provides a menu interface for collect_data.py''' 

import os, sys
from os.path import dirname, abspath

def mainMenu():
    theMenu = '''
-MAIN MENU-
    Select Option:
    (1) process JSON file(s)
    (2) view data
    (3) quit\n'''
    print theMenu
    while 1:
        response = raw_input('>>> ')
        if response in ['%s'%x for x in range (1,4)]:
            break
        if response == "quit":
            sys.exit(0)
        if response == "menu":
            print theMenu
            continue
        print ">>> ERROR: Unknown Command."
    return response

def optionMenu():
    options = []
    while 1:
        response = raw_input('Is the database on the (w)eb or (l)ocal drive?: ')
        if response == 'l' or response == 'local': # local path
            options.append(dirname(abspath(__file__)))
            options.append(True)
            break
        if response == 'w' or response == 'web': # web path
            options.append('http://sun3d.cs.princeton.edu/')
            options.append(False)
            break
        if response == "quit" or response == "exit":
            sys.exit(0)
        if response == "menu" or response == "back":
            return None
        print ">>> ERROR: Unknown Command."
    while 1:
        response = raw_input('Plot how many 3d graphs per frame? (0-360): ')
        if response.isdigit() and 0 <= int(response) <= 360:
            options.append(int(response))
            return options
        if response == "quit" or response == "exit":
            sys.exit(0)
        if response == "menu" or response == "back":
            return None
        print ">>> ERROR: Unknown Command."
