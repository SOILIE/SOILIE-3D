import json
import os, sys
import subprocess
import pandas as pd
import pprint
import random
from collections import Counter

from modules import collect_data
from modules import prepare_data
from modules import working_combos
from modules import progress_bar
from modules import png2gif
from modules.timer import *


def menuLoop():
    print ("\n#=============================#\n" + \
             "#     REDACTED  v.24.07.05    #\n" + \
             "#=============================#\n" + \
             "#      SELECT AN OPTION       #\n" + \
             "#-----------------------------#\n" + \
             "# 1. Imagine a scene          #\n" + \
             "# 2. Process input data       #\n" + \
             "# 3. Determine working combos #\n" + \
             "# 4. Exit                     #\n" + \
             "#=============================#\n")

    option = ''
    while option!='1' and option!='2' and option!='3' and \
          option!='4' and option!='exit' and option!='quit':
        option = input(">> ")

    if option=='4' or option=='exit' or option=='quit':
        sys.exit(0)

    if option=='1':
        print ("#=============================#\n" + \
               "#      SELECT AN OPTION       #\n" + \
               "#-----------------------------#\n" + \
               "# 1. Imagine a random scene   #\n" + \
               "# 2. Imagine specific objects #\n" + \
               "# 3. Imagine room types       #\n" + \
               "# 4. Back to menu             #\n" + \
               "#=============================#\n")
        generation_type = ''
        while not generation_type.isdigit():
            generation_type = input('>>> ')
            if generation_type=='4' or generation_type=='back' or generation_type=='menu':
                return menuLoop()

        if generation_type=='1': # generate random collection of objects
            same_objs = ''
            while not same_objs.lower() in ['y','yes','n','no']:
                same_objs = input("Use the same objects for every scene? ")
                if same_objs=='exit' or same_objs=='quit':
                    sys.exit(0)
                if same_objs=='menu' or same_objs=='back' or same_objs=='4':
                    return menuLoop()
            allow_multiples = ''
            while not allow_multiples.lower() in ['y','yes','n','no']:
                allow_multiples = input("Allow multiples of the same object? ")
                if allow_multiples=='exit' or allow_multiples=='quit':
                    sys.exit(0)
                if allow_multiples=='menu' or allow_multiples=='back' or allow_multiples=='4':
                    return menuLoop()
            allow_multiples = True if allow_multiples.startswith('y') else False
            if same_objs.startswith('y'):
                numObjects = '' # prompt for number of objects
                while not numObjects.isdigit() or int(numObjects)<3:
                    numObjects = input("How many objects? ")
                    if numObjects=='exit' or numObjects=='quit':
                        sys.exit(0)
                    if numObjects=='menu' or numObjects=='back':
                        return menuLoop()
                    if not numObjects.isdigit() or int(numObjects)<3:
                        print ("Invalid input: Minimum 3 objects required.")
                objects = working_combos.load(int(numObjects))
            else: # use different objects for each scene, so get a new set each time, later on
                objects = ['<VARIABLE>']

        if generation_type=='2': # Enter custom list of objects
            names = ''   # string of object names
            objects = [] # list of object names
            while not names:
                names = input("Enter some object names: ")
                if names=='exit' or names=='quit':
                    sys.exit(0)
                if names=='menu' or names=='back':
                    return menuLoop()
                if ',' in names or ';' in names:
                    print ("Invalid format: Use SPACE as delimiter.")
                    names = ''
                else: objects = names.split(' ')
            allow_multiples = True

        if generation_type=='3': # Specify room type
            print ("#=============================#\n" + \
                   "#     SELECT A ROOM TYPE      #\n" + \
                   "#-----------------------------#\n" + \
                   "# 1. Bedroom                  #\n" + \
                   "# 2. Living Room              #\n" + \
                   "# 3. Kitchen                  #\n" + \
                   "# 4. Bathroom                 #\n" + \
                   "# 5. Back to menu             #\n" + \
                   "#=============================#\n")
            roomTypeMap = {'1':'BEDROOM','2':'LIVING ROOM','3':'KITCHEN','4':'BATHROOM'}
            roomType = '' # selected room type
            while not roomType:
                roomType = input(">> ")
                if roomType=='exit' or roomType=='quit':
                    sys.exit(0)
                if roomType=='menu' or roomType=='back' or roomType=='5':
                    return menuLoop()
                roomType = roomType.replace('_',' ').strip().upper()
                roomType = roomTypeMap[roomType] if roomType in roomTypeMap else roomType
                if roomType not in ['BEDROOM','LIVING ROOM','KITCHEN','BATHROOM']:
                    roomType = ''
                else:
                    objects = [f'<{roomType}>']
            allow_multiples = True

        numScenes = '' # prompt for number of scenes
        while not numScenes.isdigit() or int(numScenes)<1:
            numScenes = input("How many scenes? ")
            if numScenes=='exit' or numScenes=='quit':
                sys.exit(0)
            if numScenes=='menu':
                return menuLoop()
            if not numScenes.isdigit() or int(numScenes)<1:
                print ("Invalid input: Minimum 1 scene required.")

        return objects, int(numScenes), allow_multiples

    if option=='2':
        run_collect_data = ''
        while run_collect_data.lower()!='y' and run_collect_data.lower()!='n':
            run_collect_data = input('Process annotation data? (y/n) ')
            if run_collect_data=='exit' or run_collect_data=='quit':
                sys.exit(0)
            if run_collect_data=='menu' or run_collect_data=='back':
                return menuLoop()
        average_triplets = ''
        while average_triplets.lower()!='y' and average_triplets.lower()!='n':
            average_triplets = input('Gather triplet data? (y/n) ')
            if average_triplets=='exit' or average_triplets=='quit':
                sys.exit(0)
            if average_triplets=='menu' or average_triplets=='back':
                return menuLoop()
        calc_obj_sizes = ''
        while calc_obj_sizes.lower()!='y' and calc_obj_sizes.lower()!='n':
            calc_obj_sizes = input('Approximate object sizes? (y/n) ')
            if calc_obj_sizes=='exit' or calc_obj_sizes=='quit':
                sys.exit(0)
            if calc_obj_sizes=='menu' or calc_obj_sizes=='back':
                return menuLoop()
        if run_collect_data.lower()=='y':
            collect_data.main()
        if average_triplets.lower()=='y':
            prepare_data.gatherTriplets()
        if calc_obj_sizes.lower()=='y':
            prepare_data.approximateObjectSizes()
        return menuLoop()

    if option=='3': # determine working combos
        working_combos.determine()
        return menuLoop()


if __name__=="__main__":
    while 1:
        comments = []
        objects, numScenes, allow_multiples = menuLoop()
        session_timer = startTimer()
        if len(objects)==1 and objects in [['<BEDROOM>'],['<LIVING ROOM>'],['<KITCHEN>'],['<BATHROOM>'],['<VARIABLE>']]:
            theme = objects[0][1:-1].lower()
            comments.append(progress_bar.random_comment())
            objs_suffix = f' with a {theme} theme... {comments[-1]}'
            variable_objs = True
            combo_set = theme.replace(' ','') if theme!='variable' else 'refined' #else 'window'
        else:
            objs_suffix = " with the following objects:\n- "+'\n- '.join(objects)
            variable_objs = False
        print (f"Imagining {numScenes if numScenes>1 else 'a'} scene{'s' if numScenes>1 else ''}"+objs_suffix)
        for i in range(numScenes):

            scene_timer = startTimer()
            progbar_prefix = f'Imagining scene {i+1}/{numScenes}'
            text_len = progress_bar.update(0, 100, progbar_prefix, length=10)

            if variable_objs:
                num_objs=0
                while num_objs<3:
                    num_objs = random.randint(3,8)
                    objects = working_combos.load(num_objs,filepath=f"./data/working-combos-{combo_set}.csv")
                    if not allow_multiples:
                        objects = list(set(objects))
                    num_objs = len(objects)

            coords = None
            attempt_count = 1
            while coords==None or not all(all(abs(c)<5 for c in o['coords']) for o in coords.values()):
                # Keep generating coordinates until they are reasonable (within 5m from 0,0,0)
                progbar_suffix = f'Triangulating coordinates for {len(objects)} objects -- Attempt {attempt_count}'
                text_len = progress_bar.update(20, 100, progbar_prefix, progbar_suffix, length=10,prev_text_len=text_len)
                coords,text_len = prepare_data.calculateCoords(objects,prev_text_len=text_len,progbar_prefix=progbar_prefix)
                coords = dict(sorted(coords.items()))
                attempt_count+=1

            comments.append(progress_bar.random_comment())
            progbar_suffix = f'''{", ".join([o.split(".")[0].replace("_"," ").title() for o in coords.keys() if o.lower() not in ["camera","floor","wall"]])}... {comments[-1]}'''
            text_len = progress_bar.update(50, 100, progbar_prefix, progbar_suffix, length=10, prev_text_len=text_len)

            command = ['blender','--background','suggested_setup.blend','--python','modules/render.py',f'{json.dumps(coords)}']
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate() # Capture the output and error streams
            if process.returncode!=0:
                text_len = progress_bar.update(100, 100, progbar_prefix,f"ERROR: Couldn't imagine this scene with {len(objects)} objects.", length=10, prev_text_len=text_len)
                print(f'Return Code: {process.returncode}')
                print(stdout)
                print(stderr)
                continue

            results = json.loads(stdout[stdout.find('{"status":'):])
            filename = os.path.join(results['path'],results['filename']+'.csv')
            df_results = pd.DataFrame.from_dict(results['data'])
            df_results.to_csv(filename,index=False)

            comments.append(progress_bar.random_comment(audience="self"))
            progbar_suffix = f'Animating scene...{comments[-1]}'
            text_len = progress_bar.update(90, 100, progbar_prefix, progbar_suffix, length=10, prev_text_len=text_len)
            png2gif.animate_output(results['filename'],results['path'])
            comments.append(progress_bar.random_comment(audience="self"))
            progbar_suffix = f'Done in {endTimerPretty(scene_timer)} ({len(objects)} objects)... {comments[-1]}'
            text_len = progress_bar.update(100, 100, progbar_prefix, progbar_suffix, length=10, prev_text_len=text_len)

        ### FINAL WRAP-UP PROCEDURE AND SENTIMENT SCORING
        sentiment,highest_possible_sentiment,normalized_sentiment,average_sentiment = progress_bar.calculate_sentiment(comments)
        sentiment_percent = round(float(normalized_sentiment*100),2)
        total_num_bars = 16
        color_bar_cutoff = total_num_bars/4 # due to there being 4 colors
        num_bars = round(normalized_sentiment*total_num_bars)
        fill = ['\033[91m█\033[0m' if num_bars<=color_bar_cutoff else
                '\033[38;5;202m█\033[0m' if num_bars<=color_bar_cutoff*2 else
                '\033[93m█\033[0m' if num_bars<=color_bar_cutoff*3 else
                '\033[92m█\033[0m'][0]
        progress_bar.update(
            sentiment+highest_possible_sentiment, highest_possible_sentiment*2,
            prefix = f'Finished imagining {numScenes} scene{"s" if numScenes>1 else ""} '+
                     f'in {endTimerPretty(session_timer)}... {progress_bar.random_comment(audience="self")}'+
                     f'\n\tAverage sentiment: {average_sentiment} ({sentiment_percent}%)',
            suffix = '',
            decimals = 1,length = total_num_bars,fill = fill,prev_text_len=text_len)

        #print(f'Finished imagining {numScenes} scene{"s" if numScenes>1 else ""} in {endTimerPretty(session_timer)}... {progress_bar.random_comment(audience="self")}')
