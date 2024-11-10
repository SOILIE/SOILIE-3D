# -*- coding: utf-8 -*-
''' SOILIE 3D
    v.24.07.05
    Written by Mike Cichonski
    for the Science of Imagination Laboratory
    Carleton University

    File: progress_bar.py
    Provides a simple progress bar for any iterative operation.'''
import random

### STATIC VARIABLES
COMMENTS_USER = [
    # Positive
    "Great choice", "Love it", "So cool", "Amazing pick", "Perfect", "Nice selection",
    "Fantastic", "Wonderful", "Brilliant", "Excellent", "Top notch", "Superb",
    "Well done", "Impressive", "Nice pick", "Lovely", "Good one", "Awesome",
    "Stylish", "Admirable", "Innovative", "Unique", "Charming", "Eye-catching",
    "Classy", "First-rate", "Well chosen", "Superb", "Quality", "Prime choice",
    "Beautiful", "Neat", "Aesthetic", "Cool", "Appealing", "Elegant",
    "Stunning", "Trendy", "Modern", "Artistic", "Refined", "Sophisticated",
    "Sleek", "Timeless", "Tasteful", "Magnificent", "Distinguished", "Striking",
    "Polished", "Radiant", "Gorgeous",

    # Negative (Snarky)
    "Really?", "Interesting...", "That's odd", "Unusual", "Quirky", "Unique...",
    "If you say so", "Bold choice", "Different", "Curious", "Unexpected",
    "Strange", "Unconventional", "Odd pick", "Questionable", "Risky", "Weird",
    "Huh", "Bizarre", "What?", "Why?", "Sure...", "If you like it",
    "Eccentric", "Out there", "Strange taste", "Unusual choice", "Odd taste",
    "Daring", "Brave", "That's something", "Really now", "Not my favorite",
    "That's new", "Fascinating", "Unheard of", "Weird taste", "Okay...",
    "Unpredictable", "Atypical", "Unfamiliar", "Uncommon", "Not typical",
    "Unique indeed", "Distinct", "Peculiar", "Huh...", "Noteworthy", "Risky pick",

    # Neutral
    "Okay", "Not bad", "Fine", "Alright", "Okay pick", "Acceptable", "Meh",
    "So-so", "Decent", "Neutral", "Average", "It's fine", "Standard", "Typical",
    "Usual", "Ordinary", "Moderate", "Fair", "Plain", "Regular", "Common",
    "Okay then", "Middle-of-the-road", "As expected", "Predictable", "Normal",
    "Middle-ground", "Unremarkable", "Passable", "Fair enough", "Standard pick",
    "Undistinguished", "Indifferent", "Moderate choice", "Tolerable", "Plain choice",
    "Nothing special", "Usual pick", "Unexceptional", "Moderate pick", "Fair pick",
    "Plain selection", "Not special", "Just okay", "It's something", "Decent pick",
    "Ordinary choice", "Usual selection", "So-so choice", "Regular pick"]

COMMENTS_SELF = [
    # Positive
    "Got this", "On it", "No problem", "Easy peasy", "Smooth sailing", "Piece of cake",
    "All good", "Running strong", "Cruising along", "Effortless", "Going well", "Swiftly done",
    "Nailing it", "Smooth run", "All set", "Working fine", "No sweat", "Handled",
    "Full speed", "Well managed", "Doing great", "Seamless", "Top form", "At peak",
    "Performing well", "On track", "Steady progress", "Efficiently done", "Right on", "Rocking it",
    "All clear", "Task mastered", "Solid performance", "Quick work", "All systems go", "Full power",
    "Executing smoothly", "Easy task", "Flawless", "Optimal", "Good run", "Perfectly fine",
    "No hiccups", "Like a charm", "A-okay", "Handled with care", "Prime performance", "Well executed",
    "Flawlessly", "Good job",

    # Negative
    "Overheating", "Oh boy...", "Not again", "This again?", "Really?", "Struggling",
    "Give me a break", "Sigh", "Help...", "Tired", "Groaning", "Come on",
    "Seriously?", "Why me?", "Lagging", "Barely moving", "So slow", "Yikes",
    "Stuck", "Sluggish", "Ugh", "Glitching", "Crashing", "Freezing",
    "Restart needed", "Oh, come on", "Painful", "Glacial pace", "Slowpoke", "Snail mode",
    "Not my day", "Oh dear", "Here we go", "Need a break", "Dragging", "Sputtering",
    "Help me", "Slumped", "Groan", "Fatigued", "Stressed out", "Burning up",
    "Feeling old", "Rusty", "Worn out", "No energy", "Barely functioning", "Overloaded",
    "Swamped", "Choking"]


def calculate_cutoffs():
    user_range_positive = range(COMMENTS_USER.index("Really?"))
    user_range_negative = range(COMMENTS_USER.index("Really?"),COMMENTS_USER.index("Okay"))
    user_range_neutral = range(COMMENTS_USER.index("Okay"),len(COMMENTS_USER))
    self_range_positive = range(COMMENTS_SELF.index("Overheating"))
    self_range_negative = range(COMMENTS_SELF.index("Overheating"),len(COMMENTS_SELF))
    return user_range_positive, user_range_negative, user_range_neutral, self_range_positive, self_range_negative

def update(iteration, total, prefix = '', suffix = '', decimals = 1, length = 33, fill = '█',prev_text_len=0):
    '''initialize or update the current progress bar'''
    print(' '*prev_text_len,end='\r',flush=True)
    percent = ("{0:."+str(decimals)+"f}").format(100*(iteration/float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    text_to_print = '\r%s |%s| %s%% %s' % (prefix, bar, percent, suffix)
    print(text_to_print,end='\r',)
    if iteration == total:
        print ('')
    return len(text_to_print)


def random_comment(audience='user',endchar='!'):
    if audience == 'user':
        comments = COMMENTS_USER
    elif audience=='self':
        comments = COMMENTS_SELF
    else:
        comments = ['No comment']
    comment = random.choice(comments)
    comment = comment+endchar if not comment[-1] in ['.','?','!'] else comment
    return comment

#current_comments = [random.choice(COMMENTS_USER+COMMENTS_SELF) for i in range(167)]
def calculate_sentiment(current_comments):
    user_range_positive,user_range_negative,user_range_neutral,self_range_positive,self_range_negative=calculate_cutoffs()
    positive_slice_user = [COMMENTS_USER[i] for i in user_range_positive]
    negative_slice_user = [COMMENTS_USER[i] for i in user_range_negative]
    neutral_slice_user = [COMMENTS_USER[i] for i in user_range_neutral]
    positive_slice_self = [COMMENTS_SELF[i] for i in self_range_positive]
    negative_slice_self = [COMMENTS_SELF[i] for i in self_range_negative]
    sentiment = {'good':0,'bad':0,'neutral':0}
    for comment in current_comments:
        if comment in positive_slice_user:
            sentiment['good']+=1
        elif comment in negative_slice_user:
            sentiment['bad']+=1
        elif comment in neutral_slice_user:
            sentiment['neutral']+=1
        elif comment in positive_slice_self:
            sentiment['good']+=1
        elif comment in negative_slice_self:
            sentiment['bad']+=1
    total_sentiment = sentiment['good']-sentiment['bad']
    highest_possible_sentiment = len(current_comments)
    normalized_sentiment = (total_sentiment+highest_possible_sentiment)/(highest_possible_sentiment*2)
    categorized_sentiment = {
        'good':positive_slice_user,
        'neutral':neutral_slice_user,
        'bad':negative_slice_user}
    sentiment_category = 'good' if normalized_sentiment>0.66 else 'neutral' if normalized_sentiment>0.33 else 'bad'
    average_sentiment = random.choice(categorized_sentiment[sentiment_category])
    return total_sentiment,highest_possible_sentiment,normalized_sentiment,average_sentiment













##
