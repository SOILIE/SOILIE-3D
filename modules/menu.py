''' SOILIE 3D
    v.24.07.05
    Written by Mike Cichonski
    With contributions from Tae Burque
    for the Science of Imagination Laboratory
    Carleton University

    File: menu.py
    This module provides a menu interface for collect_data.py'''

import json
import os, sys
from os import listdir
from os.path import dirname, abspath, join

def mainMenu():
    theMenu = '''
-MAIN MENU-
    Select Option:
    (1) process JSON file(s)
    (2) view data
    (3) quit\n'''
    print(theMenu)
    while 1:
        response = input('>>> ')
        if response in ['%s'%x for x in range (1,4)]:
            break
        if response == "quit":
            sys.exit(0)
        if response == "menu":
            print(theMenu)
            continue
        print(">>> ERROR: Unknown Command.")
    return response


def princetonLoop():
    print('\n| Princeton Dataset Options |')
    options = ['/'.join(dirname(abspath(__file__)).split('/')[:-1]),True,0,None]
    print('Is the database on the (w)eb or (l)ocal drive?: ')
    while 1:
        response = input('>>> ')
        if response == 'l' or response == 'local': # local path
            break
        if response == 'w' or response == 'web': # web path
            options[0] = 'http://sun3d.cs.princeton.edu/'
            options[1] = False
            break
        if response == "quit" or response == "exit":
            sys.exit(0)
        if response == "menu" or response == "back":
            return response
        print(">>> ERROR: Unknown Command.")

    print('To check if all 3d points are being correctly imported\n' \
          'from the database, you may choose to generate 3d plots\n' \
          'of all the data points for each frame to get a visual\n' \
          'representation. Plot how many graphs per frame? (0-360)')
    while 1:
        response = input('>>> ')
        if response.isdigit() and 0 <= int(response) <= 360:
            options[2] = int(response)
            return options
        if response == "quit" or response == "exit":
            sys.exit(0)
        if response == "menu" or response == "back":
            return response
        print(">>> ERROR: Unknown Command.")
    return options


def washingtonLoop():
    print('\n| Washington Dataset Options |')
    options = ['/'.join(dirname(abspath(__file__)).split('/')[:-1]),True,0,None]

    scenes = sorted([f for f in os.listdir('./data/washington') if f!='pc' and '.' not in f])
    all_json_files = [f for f in listdir('./json/washington') if f.endswith('.json')]
    all_json_names = [json.load(open(join('./json/washington',jFile),'rb'))['name'] for jFile in all_json_files]
    if len(set(scenes)-set(all_json_names))>0:
        print('Sample how many frames from each scene? (Cannot \n' \
              'exceed number of frames in shortest scene)')
        while 1:
            response = input('>>> ')
            if str(response).isdigit():
                options[3] = int(response)
                break
            if response=='quit' or response=='exit':
                sys.exit(0)
            if response == "menu" or response == "back":
                return response
            print(">>> ERROR: Unknown Command.")

    print('To check if all 3d points are being correctly imported\n' \
          'from the database, you may choose to generate 3d plots\n' \
          'of all the data points for each frame to get a visual\n' \
          'representation. Plot how many graphs per frame? (0-360)')
    while 1:
        response = input('>>> ')
        if response.isdigit() and 0 <= int(response) <= 360:
            options[2] = int(response)
            return options
        if response == "quit" or response == "exit":
            sys.exit(0)
        if response == "menu" or response == "back":
            return response
        print(">>> ERROR: Unknown Command.")
    return options


def optionMenu():
    print('Please select dataset:\n1. All Available Datasets\n2. SUN3D - Princeton\n' +
          '3. RGB-D Scenes Dataset - Washington\n4. Other (Please specify)')
    options = {}
    response = ''
    while response not in ['1','2','3']:
        response = input('>>> ')
        if response == "quit" or response == "exit":
            sys.exit(0)
        if response == "menu" or response == "back":
            return response
        datasets = ['princeton','washington'] if response=='1' else ['princeton'] if response=='2' else ['washington']

    for datasetName in datasets:
        if datasetName=='princeton':
            response = princetonLoop()
            if response == "quit" or response == "exit":
                sys.exit(0)
            if response == "menu" or response == "back":
                return response
            options[datasetName] = response
        if datasetName=='washington':
            response = washingtonLoop()
            if response == "quit" or response == "exit":
                sys.exit(0)
            if response == "menu" or response == "back":
                return response
            options[datasetName] = response

    return options
