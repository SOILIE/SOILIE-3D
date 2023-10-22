import json
import os, sys

from modules import collect_data
from modules import prepare_data
from modules import working_combos

def menuLoop():
    print ("\n#=============================#\n" + \
             "#     SOILIE 3D v.23.10.31    #\n" + \
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
               "# 2. Specify object names     #\n" + \
               "# 3. Back to menu             #\n" + \
               "#=============================#\n")
        generation_type = ''
        while not generation_type.isdigit():
            generation_type = input('>>> ')
            if generation_type=='3' or generation_type=='back' or generation_type=='menu':
                return menuLoop()

        if generation_type=='1': # generate random collection of objects
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

        numScenes = '' # prompt for number of scenes
        while not numScenes.isdigit() or int(numScenes)<1:
            numScenes = input("How many scenes? ")
            if numScenes=='exit' or numScenes=='quit':
                sys.exit(0)
            if numScenes=='menu':
                return menuLoop()
            if not numScenes.isdigit() or int(numScenes)<1:
                print ("Invalid input: Minimum 1 scene required.")

        return objects, int(numScenes)

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
        objects, numScenes = menuLoop()
        print (f"INFO: Imagining {numScenes if numScenes>1 else 'a'} scene{'s' if numScenes>1 else ''} with the following objects:\n- "+'\n- '.join(objects))
        for i in range(numScenes):
            print(f'Imagining scene {i+1}/{numScenes}...')
            coords = prepare_data.calculateCoords(objects)
            os.system(f"blender --background suggested_setup.blend --python modules/render.py '{json.dumps(coords)}'")
        print(f'Finished imagining scenes!')
