import torch
import torch.nn as nn
from torchvision import models
from torch.utils.data import DataLoader
import os
import json
import numpy as np
import argparse
import pandas as pd
import probabilities_to_decision
import helper.human_categories as sc
import matplotlib.pyplot as plt
import glob
import math
import random
from scipy import spatial
from data import GeirhosStyleTransferDataset, GeirhosTriplets


def plot_class_values(categories, class_values, im, shape, texture, model_type):
    """This function plots the values that the model assigns to the Geirhos
    Style Transfer classes (airplane, bear, ..., oven, truck; 16 total).

    :param categories: a list of the 16 Geirhos classes, either organized by shape or
        texture.
    :param class_values: A length 16 vector. The values referred to here are those
        calculated by the Geirhos probability mapping code, which takes the 1000-length
        vector output of the model as an input; it then groups the various ImageNet classes
        into groups that correspond with a single Geirhos class, and it takes the average of
        the probabilities amongst this group of ImageNet classes. This average becomes
        the value assigned to the Geirhos class, and the class receiving the highest average
        probability is the model's decision.
    :param im: the path of the image file that produced these results.
    :param shape: the shape classification of the given image.
    :param texture: the texture classification of the given image."""

    decision_idx = class_values.index(max(class_values))  # index of maximum class value
    decision = categories[decision_idx]
    shape_idx = categories.index(shape)  # index of shape category
    texture_idx = categories.index(texture)  # index of texture category

    spec = plt.GridSpec(ncols=2, nrows=1, width_ratios=[4, 1], wspace=0.2, )

    fig = plt.figure()
    fig.set_figheight(6)
    fig.set_figwidth(9.5)

    # Bar plot
    fig.add_subplot(spec[0])
    plt.bar(categories, class_values, color=(0.4, 0.4, 0.4), width=0.4)
    plt.bar(categories[decision_idx], class_values[decision_idx],
            color=(0.9411764705882353, 0.00784313725490196, 0.4980392156862745), width=0.4)
    plt.bar(categories[shape_idx], class_values[shape_idx],
            color=(0.4980392156862745, 0.788235294117647, 0.4980392156862745), width=0.4)
    plt.bar(categories[texture_idx], class_values[texture_idx],
            color=(0.7450980392156863, 0.6823529411764706, 0.8313725490196079), width=0.4)
    plt.xlabel("Geirhos Style Transfer class", fontsize=12)
    plt.ylabel("Average probabilities across associated ImageNet classes", fontsize=10)
    plt.suptitle("Model decision for " + im + ":", fontsize=15)
    plt.title("Model Outputs", fontsize=12)
    plt.xticks(rotation=45)
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])

    # Create the legend
    colors = {'model decision: ' + decision: (0.9411764705882353, 0.00784313725490196, 0.4980392156862745),
              'shape category: ' + shape: (0.4980392156862745, 0.788235294117647, 0.4980392156862745),
              'texture category: ' + texture: (0.7450980392156863, 0.6823529411764706, 0.8313725490196079)}
    labels = list(colors.keys())
    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[label]) for label in labels]
    plt.legend(handles, labels)

    # Plot the image
    im_ax = fig.add_subplot(spec[1])
    img = plt.imread('stimuli-shape/style-transfer/' + shape + '/' + im)
    plt.imshow(img)
    plt.title(im)
    im_ax.set_xticks([])
    im_ax.set_yticks([])

    plt.savefig('figures/' + model_type + '/' + im)


def csv_class_values(shape_dict, shape_categories, shape_spec_dict, csv_dir):
    """Writes the shape category, texture category, and model decision for all
    shape-texture combinations in a given Geirhos shape class to a CSV file.
    Also includes whether or not neither the shape or texture classification is made.

    :param shape_dict: a dictionary of values with shape category keys. Should
        store the decision made, the length 16 vector of class values for a
        given shape-image combination, and the decision made when restricted to
        only the shape and texture categories.
    :param shape_categories: a list of all Geirhos shape classes.
    :param shape_spec_dict: a shape-indexed dictionary of lists, each containing
        the specific textures for a given shape (eg. clock1, oven2, instead of
        just clock, oven, etc). This ensures that results for clock1 and clock2
        for example do not overwrite each other.
    :param csv_dir: directory for storing the CSV."""

    columns = ['Shape', 'Texture', 'Decision', 'Shape Category Value', 'Texture Category Value',
               'Decision Category Value', 'Shape Decision', 'Texture Decision', 'Neither',
               'Restricted Decision', 'Restriced Shape Value', 'Restricted Texture Value',
               'Restricted Shape Decision', 'Restricted Texture Decision']

    for shape in shape_categories:
        specific_textures = shape_spec_dict[shape]
        df = pd.DataFrame(index=range(len(specific_textures)), columns=columns)
        df['Shape'] = shape

        for i, row in df.iterrows():
            texture = specific_textures[i]
            decision = shape_dict[shape][texture + '0'][0]
            class_values = shape_dict[shape][texture + '0'][1]
            decision_restricted = shape_dict[shape][texture + '0'][2]
            restricted_class_values = shape_dict[shape][texture + '0'][3]

            row['Texture'] = texture
            row['Decision'] = decision
            row['Shape Category Value'] = class_values[shape_categories.index(shape)]
            row['Texture Category Value'] = class_values[shape_categories.index(texture[:-1:])]
            row['Decision Category Value'] = class_values[shape_categories.index(decision)]

            row['Shape Decision'] = int(decision == shape)
            row['Texture Decision'] = int(decision == texture[:-1:])
            row['Neither'] = int(decision != shape and decision != texture[:-1:])

            row['Restricted Decision'] = decision_restricted
            row['Restricted Shape Decision'] = int(shape == decision_restricted)

            row['Restricted Texture Decision'] = int(texture[:-1:] == decision_restricted)
            row['Restricted Shape Value'] = restricted_class_values[0]
            row['Restricted Texture Value'] = restricted_class_values[1]

        df.to_csv(csv_dir + '/' + shape + '.csv', index=False)


def calculate_totals(shape_categories, result_dir, verbose=False):
    """Calculates the total number of shape, texture, and neither shape nor
    texture decisions by Geirhos shape class (and overall). Stores these
    results in a CSV and optionally prints them out.

    :param shape_categories: a list of Geirhos shape classes.
    :param result_dir: where to store the results.
    :param verbose: True if you want to print the results as well as store them."""

    shape_dict = dict.fromkeys(shape_categories)
    texture_dict = dict.fromkeys(shape_categories)
    neither_dict = dict.fromkeys(shape_categories)
    restricted_shape_dict = dict.fromkeys(shape_categories)
    restricted_texture_dict = dict.fromkeys(shape_categories)

    columns = ['Shape Category', 'Number Shape Decisions', 'Number Texture Decisions',
               'Number Neither', 'Number Restricted Shape Decisions',
               'Number Restricted Texture Decisions', 'Total Number Stimuli']
    result_df = pd.DataFrame(columns=columns, index=range(len(shape_categories) + 1))

    for shape in shape_categories:
        shape_dict[shape] = 0
        texture_dict[shape] = 0
        neither_dict[shape] = 0
        restricted_shape_dict[shape] = 0
        restricted_texture_dict[shape] = 0

    for filename in os.listdir(result_dir):
        if filename[-4:] != '.csv' or filename == 'totals.csv':
            continue

        df = pd.read_csv(result_dir + '/' + filename)
        shape = df['Shape'][0]
        for i, row in df.iterrows():
            if row['Restricted Shape Decision'] != row['Restricted Texture Decision']:
                shape_dict[shape] = shape_dict[shape] + row['Shape Decision']
                texture_dict[shape] += row['Texture Decision']
                neither_dict[shape] += row['Neither']
                restricted_shape_dict[shape] += row['Restricted Shape Decision']
                restricted_texture_dict[shape] += row['Restricted Texture Decision']

    for shape in shape_categories:
        if verbose:
            print("Shape category: " + shape)
            print("\tNumber shape decisions: " + str(shape_dict[shape]))
            print("\tNumber texture decisions: " + str(texture_dict[shape]))
            print("\tNumber neither shape nor texture decisions: " + str(neither_dict[shape]))
            print("\t---------------------------------------------")
            print("\tNumber shape decisions (restricted to only shape/texture classes): "
                  + str(restricted_shape_dict[shape]))
            print("\tNumber texture decisions (restricted to only shape/texture classes): "
                  + str(restricted_texture_dict[shape]))
            print()

        shape_idx = shape_categories.index(shape)
        result_df.at[shape_idx, 'Shape Category'] = shape
        result_df.at[shape_idx, 'Number Shape Decisions'] = shape_dict[shape]
        result_df.at[shape_idx, 'Number Texture Decisions'] = texture_dict[shape]
        result_df.at[shape_idx, 'Number Neither'] = neither_dict[shape]
        result_df.at[shape_idx, 'Number Restricted Shape Decisions'] = restricted_shape_dict[shape]
        result_df.at[shape_idx, 'Number Restricted Texture Decisions'] = restricted_texture_dict[shape]
        result_df.at[shape_idx, 'Total Number Stimuli'] = shape_dict[shape] + texture_dict[shape] +\
                                                          neither_dict[shape]

    if verbose:
        print("IN TOTAL:")
        print("\tNumber shape decisions: " + str(sum(shape_dict.values())))
        print("\tNumber texture decisions: " + str(sum(texture_dict.values())))
        print("\tNumber neither shape nor texture decisions: " + str(sum(neither_dict.values())))
        print("\t---------------------------------------------")
        print("\tNumber shape decisions (restricted to only shape/texture classes): "
              + str(sum(restricted_shape_dict.values())))
        print("\tNumber texture decisions (restricted to only shape/texture classes): "
              + str(sum(restricted_texture_dict.values())))
        print()

    idx = len(shape_categories)  # final row
    result_df.at[idx, 'Shape Category'] = 'total'
    result_df.at[idx, 'Number Shape Decisions'] = sum(shape_dict.values())
    result_df.at[idx, 'Number Texture Decisions'] = sum(texture_dict.values())
    result_df.at[idx, 'Number Neither'] = sum(neither_dict.values())
    result_df.at[idx, 'Total Number Stimuli'] = sum(neither_dict.values()) + \
                                                sum(texture_dict.values()) + sum(shape_dict.values())
    result_df.at[idx, 'Number Restricted Shape Decisions'] = sum(restricted_shape_dict.values())
    result_df.at[idx, 'Number Restricted Texture Decisions'] = sum(restricted_texture_dict.values())

    result_df.to_csv(result_dir + '/totals.csv', index=False)


def calculate_proportions(result_dir, verbose=False):
    """Calculates the proportions of shape and texture decisions for a given model.
    There are two proportions calculated for both shape and texture: 1) with neither
    shape nor texture decisions included, and 2) without considering 'neither'
    decisions. Stores these proportions in a text file and optionally prints them.

    :param result_dir: the directory of the results for the model."""

    df = pd.read_csv(result_dir + '/totals.csv')
    row = df.loc[df['Shape Category'] == 'total']
    shape = int(row['Number Shape Decisions'])
    texture = int(row['Number Texture Decisions'])
    total = int(row['Total Number Stimuli'])

    shape_restricted = int(row['Number Restricted Shape Decisions']) / total
    texture_restricted = int(row['Number Restricted Texture Decisions']) / total

    shape_texture = shape / (shape + texture)
    texture_shape = texture / (shape + texture)
    shape_all = shape / total
    texture_all = texture / total

    strings = ["Proportion of shape decisions (disregarding 'neither' decisions): " + str(shape_texture),
               "Proportion of texture decisions (disregarding 'neither' decisions): " + str(texture_shape),
               "Proportion of shape decisions (including 'neither' decisions): " + str(shape_all),
               "Proportion of texture decisions (including 'neither' decisions): " + str(texture_all),
               "Proportion of shape decisions (restricted to only shape/texture classes): " + str(shape_restricted),
               "Proportion of texture decisions (restricted to only shape/texture classes): " + str(texture_restricted)]
    file = open(result_dir + '/proportions.txt', 'w')

    for i in range(len(strings)):
        file.write(strings[i] + '\n')
        if verbose:
            print(strings[i])

    file.close()


def get_penultimate_layer(model, image):
    """Extracts the activations of the penultimate layer for a given input
    image.

    :param model: the model to extract activations from
    :param image: the image to be passed through the model
    :return: the activation of the penultimate layer"""

    layer = model._modules.get('avgpool')
    activations = torch.zeros(2048)

    def copy_data(m, i, o):
        activations.copy_(o.data)

    h = layer.register_forward_hook(copy_data)

    model(image)

    h.remove()

    return activations


def plot_similarity_histograms(model_type):
    """First plots 6 regular histograms: one set of 2 for cosine similarity between anchor
    images and shape/texture matches, one set of 2 for dot product between anchor images
    and shape/texture matches, and one set of 2 for Euclidean distance between anchor images
    and shape/texture matches (across all classes). Then plots a set of two "difference"
    histograms, which plot the difference between the cosine similarity, dot product, or
    Euclidean distance to the shape match and texture match (eg. cos_difference =
    cos_similarity(anchor, shape match) - cos_similarity(anchor, texture match)).

    :param model_type: saycam, resnet50, etc.
    """

    # Create directory
    plot_dir = 'figures/' + model_type + '/similarity'
    try:
        os.mkdir(plot_dir)
    except FileExistsError:
        pass
    plot_dir += '/'

    # Collect data
    sim_dir = 'results/' + model_type + '/similarity/'

    shape_dot = []
    shape_cos = []
    shape_ed = []
    texture_dot = []
    texture_cos = []
    texture_ed = []
    cos_difference = []
    dot_difference = []
    ed_difference = []

    for file in glob.glob(sim_dir + '*.csv'):

        if file == sim_dir + 'averages.csv' or file == sim_dir + 'proportions.csv' \
                or file == sim_dir + 'matrix.csv':
            continue
        df = pd.read_csv(file)

        for index, row in df.iterrows():
            shape_dot.append(float(row['Shape Dot']))
            shape_cos.append(float(row['Shape Cos']))
            shape_ed.append(float(row['Shape ED']))
            texture_dot.append(float(row['Texture Dot']))
            texture_cos.append(float(row['Texture Cos']))
            texture_ed.append(float(row['Texture ED']))

            cos_difference.append(float(row['Shape Cos']) - float(row['Texture Cos']))
            dot_difference.append(float(row['Shape Dot']) - float(row['Texture Dot']))
            ed_difference.append(float(row['Shape ED']) - float(row['Texture ED']))

    # Plot regular histograms
    fig, axs = plt.subplots(3, 2)
    fig.set_figheight(14)
    fig.set_figwidth(12)
    plt.suptitle('Histogram of Similarities Between Anchor Images & Shape/Texture Matches',
                 fontsize='xx-large')

    y1, x1, _ = axs[0, 0].hist(shape_cos, color='#ffb4b4', bins=30)
    axs[0, 0].set_title('Cosine Similarity: Shape Match')

    y2, x2, _ = axs[0, 1].hist(texture_cos, color='#ff7694', bins=30)
    axs[0, 1].set_ylim([0, max(y1.max(), y2.max() + 1000)])
    axs[0, 0].set_ylim([0, max(y1.max(), y2.max()) + 1000])
    axs[0, 1].set_title('Cosine Similarity: Texture Match')

    y3, x3, _ = axs[1, 0].hist(shape_dot, color='#ba6ad0', bins=30)
    axs[1, 0].set_title('Dot Product: Shape Match')

    y4, x4, _ = axs[1, 1].hist(texture_dot, color='#645f97', bins=30)
    axs[1, 0].set_ylim([0, max(y3.max(), y4.max()) + 1000])
    axs[1, 1].set_ylim([0, max(y3.max(), y4.max()) + 1000])
    axs[1, 1].set_title('Dot Product: Texture Match')

    y5, x5, _ = axs[2, 0].hist(shape_ed, color='#fee8ca', bins=30)
    axs[2, 0].set_title('Euclidean Distance: Shape Match')

    y6, x6, _ = axs[2, 1].hist(texture_ed, color='#cb8f32', bins=30)
    axs[2, 0].set_ylim([0, max(y5.max(), y6.max()) + 1000])
    axs[2, 1].set_ylim([0, max(y5.max(), y6.max()) + 1000])
    axs[2, 1].set_title('Euclidean Distance: Texture Match')

    plt.savefig(plot_dir + 'regular.png')

    # Plot difference histograms
    fig, axs = plt.subplots(1, 3)
    fig.set_figheight(6)
    fig.set_figwidth(18)
    plt.suptitle('Histogram of Difference between Shape & Texture Match Similarities', fontsize='xx-large')
    
    y7, x7, _ = axs[0].hist(cos_difference, color='#ff7694', bins=30)
    axs[0].set_title('Cosine Similarity Difference: Shape - Texture')
    
    y8, x8, _ = axs[1].hist(dot_difference, color='#ffb4b4', bins=30)
    axs[1].set_title('Dot Product Difference: Shape - Texture')

    y9, x9, _ = axs[2].hist(ed_difference, color='#fee8ca', bins=30)
    axs[2].set_title('Euclidean Distance Difference: Shape - Texture')
    
    plt.savefig(plot_dir + 'difference.png')


def generate_fake_triplets(model_type, model, shape_dir, n=230431):
    '''Generates fake embeddings that have the same dimensionality as
     model_type for n triplets, then calculates cosine similarity & dot product
     statistics.

     :param model_type: resnet50, saycam, etc.
     :param n: number of fake triplets to generate. Default is the number of
               triplets the real models see.'''

    # Retrieve embedding magnitude statistics from the real model
    try:
        embeddings = json.load(open('embeddings/' + model_type + '_embeddings.json'))
    except FileNotFoundError:
        embeddings = get_embeddings(shape_dir, model, model_type)

    avg = 0
    num_embeddings = 0
    min_e = math.inf
    max_e = 0

    size = len(list(embeddings.values())[0])

    for embedding in embeddings.values():
        num_embeddings += 1
        mag = np.linalg.norm(embedding)
        avg += mag
        if mag > max_e:
            max_e = mag
        if mag < min_e:
            min_e = mag

    avg = avg / num_embeddings

    columns = ['Model', 'Anchor', 'Shape Match', 'Texture Match',
               'Shape Dot', 'Shape Cos', 'Texture Dot', 'Texture Cos'
               'Shape Dot Closer', 'Shape Cos Closer', 'Texture Dot Closer', 'Texture Cos Closer']
    results = pd.DataFrame(index=range(n), columns=columns)

    try:
        os.mkdir('results/' + model_type +'/similarity/fake')
    except FileExistsError:
        pass

    #print("average magnitude: " + str(avg))
    #print("max: " + str(max_e))
    #print("min: " + str(min_e))
    #print("range: " + str(max_e - min_e))

    # Iterate over n fake triplets
    for t in range(n):
        anchor = []
        shape_match = []
        texture_match = []

        lists = [anchor, shape_match, texture_match]
        new_lists = []

        # Generate three random vectors
        for l in lists:

            for idx in range(size):
                l.append(random.random())

            mag = -1
            while mag < 0:
                mag = np.random.normal(loc=avg, scale=min(avg - min_e, max_e - avg) / 2)

            l = np.array(l)
            current_mag = np.linalg.norm(l)
            new_l = (mag * l) / current_mag
            new_lists.append(new_l)

        anchor = new_lists[0]
        shape_match = new_lists[1]
        texture_match = new_lists[2]

        #print(np.linalg.norm(anchor))
        #print(np.linalg.norm(shape_match))
        #print(np.linalg.norm(texture_match))

        results.at[t, 'Anchor'] = anchor
        results.at[t, 'Shape Match'] = shape_match
        results.at[t, 'Texture Match'] = texture_match

        shape_dot = np.dot(anchor, shape_match)
        shape_cos = spatial.distance.cosine(anchor, shape_match)
        texture_dot = np.dot(anchor, texture_match)
        texture_cos = spatial.distance.cosine(anchor, texture_match)

        results.at[t, 'Shape Dot'] = shape_dot
        results.at[t, 'Shape Cos'] = shape_cos
        results.at[t, 'Texture Dot'] = texture_dot
        results.at[t, 'Texture Cos'] = texture_cos

        if shape_dot > texture_dot:
            results.at[t, 'Shape Dot Closer'] = 1
            results.at[t, 'Texture Dot Closer'] = 0
        else:
            results.at[t, 'Shape Dot Closer'] = 0
            results.at[t, 'Texture Dot Closer'] = 1

        if shape_cos > texture_cos:
            results.at[t, 'Shape Cos Closer'] = 1
            results.at[t, 'Texture Cos Closer'] = 0
        else:
            results.at[t, 'Shape Cos Closer'] = 0
            results.at[t, 'Texture Cos Closer'] = 1

    results.to_csv('results/' + model_type +'/similarity/fake/fake.csv')
    calculate_similarity_totals(model_type, fake=True)


def calculate_similarity_totals(model_type, matrix=False, fake=False):
    """Calculates proportion of times the shape/texture dot product/cosine similarity
    is closer for a given model. Stores proportions as a csv.

    :param model_type: saycam, resnet50, etc.
    :param matrix: true if you want to calculate a matrix of totals instead of
                   proportions."""

    if not fake:
        sim_dir = 'results/' + model_type + '/similarity/'
    else:
        sim_dir = 'results/' + model_type +'/similarity/fake/'

    if not matrix:
        columns = ['Model', 'Shape Dot Closer', 'Shape Cos Closer', 'Texture Dot Closer', 'Texture Cos Closer',
                   'Shape ED Closer', 'Texture ED Closer']
        results = pd.DataFrame(index=range(1), columns=columns)

        shape_dot = 0
        shape_cos = 0
        shape_ed = 0
        texture_dot = 0
        texture_cos = 0
        texture_ed = 0
        num_rows = 0

    else:
        # Matrix: [0, 0] = shape match w/ dot and shape match w/ cos
        # [0, 1] = texture match w/ dot and shape match w/ cos
        # [1, 0] = shape match w/ dot and texture match w/ cos
        # [1, 1] = texture match w/ dot and texture match w/ cos
        columns = ['Model', ' ', 'Shape Match with Dot Product', 'Texture Match with Dot Product']
        results = pd.DataFrame(index=range(2), columns=columns)
        results.at[0, ' '] = 'Shape Match with Cosine Similarity'
        results.at[1, ' '] = 'Texture Match with Cosine Similarity'

        m0_0 = 0
        m0_1 = 0
        m1_0 = 0
        m1_1 = 0

    results.at[:, 'Model'] = model_type

    for file in glob.glob(sim_dir + '*.csv'):

        if file == sim_dir + 'averages.csv' or file == sim_dir + 'proportions.csv'\
                or file == sim_dir + 'matrix.csv':
            continue
        df = pd.read_csv(file)

        for index, row in df.iterrows():
            shape_dot_closer = int(row['Shape Dot Closer'])
            shape_cos_closer = int(row['Shape Cos Closer'])
            shape_ed_closer = int(row['Shape ED Closer'])
            texture_dot_closer = int(row['Texture Dot Closer'])
            texture_cos_closer = int(row['Texture Cos Closer'])
            texture_ed_closer = int(row['Texture ED Closer'])

            if not matrix:
                shape_dot += shape_dot_closer
                shape_cos += shape_cos_closer
                shape_ed += shape_ed_closer
                texture_dot += texture_dot_closer
                texture_cos += texture_cos_closer
                texture_ed += texture_ed_closer
                num_rows += 1

            else:
                if shape_dot_closer == 1:
                    if shape_cos_closer == 1:
                        m0_0 += 1
                    elif texture_cos_closer == 1:
                        m1_0 += 1
                elif texture_dot_closer == 1:
                    if shape_cos_closer == 1:
                        m0_1 += 1
                    elif texture_cos_closer == 1:
                        m1_1 += 1

    if not matrix:
        results.at[0, 'Shape Dot Closer'] = shape_dot / num_rows
        results.at[0, 'Shape Cos Closer'] = shape_cos / num_rows
        results.at[0, 'Shape ED Closer'] = shape_ed / num_rows
        results.at[0, 'Texture Dot Closer'] = texture_dot / num_rows
        results.at[0, 'Texture Cos Closer'] = texture_cos / num_rows
        results.at[0, 'Texture ED Closer'] = texture_ed / num_rows

        results.to_csv(sim_dir + 'proportions.csv', index=False)
    else:
        results.at[0, 'Shape Match with Dot Product'] = m0_0
        results.at[1, 'Shape Match with Dot Product'] = m1_0
        results.at[0, 'Texture Match with Dot Product'] = m0_1
        results.at[1, 'Texture Match with Dot Product'] = m1_1

        results.to_csv(sim_dir + 'matrix.csv', index=False)


def calculate_similarity_averages(model_type, shape_categories, plot):
    """Calculates average dot product/cosine similarity between an anchor image shape
    class and its shape/texture matches. Stores results in a csv. Optionally generates
    a plot.

    :param model_type: resnet50, saycam, etc.
    :param shape_categories: a list of the 16 Geirhos classes.
    :param plot: true if plot should be generated.
    """

    columns = ['Model', 'Anchor Image Shape', 'Average Dot Shape', 'Average Cos Shape',
               'Average Dot Texture', 'Average Cos Texture']
    results = pd.DataFrame(index=range(len(shape_categories)), columns=columns)
    result_dir = 'results/' + model_type + '/similarity'

    results.at[:, 'Model'] = model_type

    for i in range(len(shape_categories)):  # Iterate over anchor image shapes
        anchor_shape = shape_categories[i]

        shape_dot = 0
        shape_cos = 0
        texture_dot = 0
        texture_cos = 0
        num_triplets = 0

        for file in glob.glob(result_dir + '/' + anchor_shape + '*.csv'):  # Sum results by shape
            df = pd.read_csv(file)

            for index, row in df.iterrows():
                shape_dot += float(row['Shape Dot'])
                shape_cos += float(row['Shape Cos'])
                texture_dot += float(row['Texture Dot'])
                texture_cos += float(row['Texture Cos'])
                num_triplets += 1

        shape_dot = shape_dot / num_triplets
        shape_cos = shape_cos / num_triplets
        texture_dot = texture_dot / num_triplets
        texture_cos = texture_cos / num_triplets

        results.at[i, 'Anchor Image Shape'] = anchor_shape
        results.at[i, 'Average Dot Shape'] = shape_dot
        results.at[i, 'Average Cos Shape'] = shape_cos
        results.at[i, 'Average Dot Texture'] = texture_dot
        results.at[i, 'Average Cos Texture'] = texture_cos

    results.to_csv(result_dir + '/averages.csv', index=False)


def triplets(model_type, embeddings, verbose, shape_dir):
    """First generates all possible triplets of the following form:
    (anchor image, shape match, texture match). Then retrieves the activations
    of the penultimate layer of a given model for each image in the triplet.
    Finally, computes and stores cosine similarity, dot products, Euclidean distances:
    anchor x shape match, anchor x texture match. This determines whether the model
    thinks the shape or texture match for an anchor image is closer to the anchor and
    essentially provides a secondary measure of shape/texture bias.

    :param model_type: resnet50, saycam, etc.
    :param embeddings: a dictionary of embeddings for each image for the given model
    :param verbose: true if results should be printed to the terminal.
    :param shape_dir: directory for the Geirhos dataset."""

    t = GeirhosTriplets(shape_dir)
    images = t.shape_classes.keys()
    all_triplets = t.triplets_by_image

    sim_dict = {}

    columns = ['Model', 'Anchor', 'Anchor Shape', 'Anchor Texture', 'Shape Match',
               'Texture Match', 'Shape Dot', 'Shape Cos', 'Shape ED',
               'Texture Dot', 'Texture Cos', 'Texture ED', 'Shape Dot Closer',
               'Shape Cos Closer', 'Shape ED Closer',
               'Texture Dot Closer', 'Texture Cos Closer', 'Texture ED Closer']

    cosx = torch.nn.CosineSimilarity(dim=0, eps=1e-08)

    for anchor in images:  # Iterate over possible anchor images
        anchor_triplets = all_triplets[anchor]['triplets']
        num_triplets = len(anchor_triplets)

        df = pd.DataFrame(index=range(num_triplets), columns=columns)
        df['Anchor'] = anchor[:-4]
        df['Model'] = model_type
        df['Anchor Shape'] = t.shape_classes[anchor]['shape_spec']
        df['Anchor Texture'] = t.shape_classes[anchor]['texture_spec']

        for i in range(num_triplets):  # Iterate over possible triplets
            triplet = anchor_triplets[i]
            shape_match = triplet[1]
            texture_match = triplet[2]

            df.at[i, 'Shape Match'] = shape_match[:-4]
            df.at[i, 'Texture Match'] = texture_match[:-4]

            # Retrieve images corresponding to names
            # anchor_im, shape_im, texture_im = t.getitem(triplet)

            # Get image embeddings
            anchor_output = torch.FloatTensor(embeddings[anchor])
            shape_output = torch.FloatTensor(embeddings[shape_match])
            texture_output = torch.FloatTensor(embeddings[texture_match])

            # Retrieve similarities if they've already been calculated
            if (anchor, shape_match) in sim_dict.keys() or (shape_match, anchor) in sim_dict.keys():
                try:
                    shape_dot = sim_dict[(anchor, shape_match)][0]
                    shape_cos = sim_dict[(anchor, shape_match)][1]
                    shape_ed = sim_dict[(anchor, shape_match)][2]
                except KeyError:
                    shape_dot = sim_dict[(shape_match, anchor)][0]
                    shape_cos = sim_dict[(shape_match, anchor)][1]
                    shape_ed = sim_dict[(shape_match, anchor)][2]
            else:
                shape_dot = np.dot(anchor_output, shape_output)
                shape_cos = cosx(anchor_output, shape_output)
                shape_ed = torch.cdist(torch.unsqueeze(shape_output, 0), torch.unsqueeze(anchor_output, 0))
                sim_dict[(anchor, shape_match)] = [shape_dot, shape_cos, shape_ed]

            if (anchor, texture_match) in sim_dict.keys() or (texture_match, anchor) in sim_dict.keys():
                try:
                    texture_dot = sim_dict[(anchor, texture_match)][0]
                    texture_cos = sim_dict[(anchor, texture_match)][1]
                    texture_ed = sim_dict[(anchor, texture_match)][2]
                except KeyError:
                    texture_dot = sim_dict[(texture_match, anchor)][0]
                    texture_cos = sim_dict[(texture_match, anchor)][1]
                    texture_ed = sim_dict[(texture_match, anchor)][2]
            else:
                texture_dot = np.dot(anchor_output, texture_output)
                texture_cos = cosx(anchor_output, texture_output)
                texture_ed = torch.cdist(torch.unsqueeze(texture_output, 0), torch.unsqueeze(anchor_output, 0))
                sim_dict[(anchor, texture_match)] = [texture_dot, texture_cos, texture_ed]

            if verbose:
                print("For " + anchor + " paired with " + shape_match + ", " + texture_match + ":")
                print("\tShape match dot product: " + str(shape_dot))
                print("\tShape match cos similarity: " + str(shape_cos.item()))
                print("\tShape match Euclidean distance: " + str(shape_ed.item()))
                print("\t-------------")
                print("\tTexture match dot: " + str(texture_dot))
                print("\tTexture match cos similarity: " + str(texture_cos.item()))
                print("\tTexture match Euclidean distance: " + str(texture_ed.item()))
                print()

            df.at[i, 'Shape Dot'] = shape_dot
            df.at[i, 'Shape Cos'] = shape_cos.item()
            df.at[i, 'Shape ED'] = shape_ed.item()
            df.at[i, 'Texture Dot'] = texture_dot
            df.at[i, 'Texture Cos'] = texture_cos.item()
            df.at[i, 'Texture ED'] = texture_ed.item()

            # Compare shape/texture results
            if shape_dot > texture_dot:
                df.at[i, 'Shape Dot Closer'] = 1
                df.at[i, 'Texture Dot Closer'] = 0
            else:
                df.at[i, 'Shape Dot Closer'] = 0
                df.at[i, 'Texture Dot Closer'] = 1

            if shape_cos > texture_cos:
                df.at[i, 'Shape Cos Closer'] = 1
                df.at[i, 'Texture Cos Closer'] = 0
            else:
                df.at[i, 'Shape Cos Closer'] = 0
                df.at[i, 'Texture Cos Closer'] = 1

            if shape_ed < texture_ed:
                df.at[i, 'Shape ED Closer'] = 1
                df.at[i, 'Texture ED Closer'] = 0
            else:
                df.at[i, 'Shape ED Closer'] = 0
                df.at[i, 'Texture ED Closer'] = 1

        df.to_csv('results/' + model_type + '/similarity/' + anchor[:-4] + '.csv', index=False)


def get_embeddings(dir, model, model_type, self_supervised=False):
    """ Retrieves embeddings for each image in a dataset from the penultimate
    layer of a given model. Stores the embeddings in a dictionary (indexed by
    image name, eg. cat4-truck3). Returns the dictionary and stores it in a json
    file (model_type_embeddings.json)

    :param dir: path of the dataset
    :param model: the model to extract the embeddings from
    :param model_type: the type of model, eg. saycam, resnet50, etc.
    :param self_supervised: True if the model being passed is self supervised.

    :return: a dictionary indexed by image name that contains the embeddings for
        all images in a dataset extracted from the penultimate layer of a given
        model.
    """

    try:
        os.mkdir('embeddings')
    except FileExistsError:
        pass

    # Initialize dictionary
    embedding_dict = {}

    # Initialize dataset
    dataset = GeirhosStyleTransferDataset(dir, '')
    num_images = dataset.__len__()

    softmax = nn.Softmax(dim=1)

    # Remove the final layer from the model
    if self_supervised:
        penult_model = model
    else:
        if model_type == 'saycam' or model_type == 'saycamA' or model_type == 'saycamS'\
                or model_type == 'saycamY':
            modules = list(model.module.children())[:-1]
            penult_model = nn.Sequential(*modules)
        elif model_type == 'resnet50':
            modules = list(model.children())[:-1]
            penult_model = nn.Sequential(*modules)

        for p in penult_model.parameters():
            p.requires_grad = False

    with torch.no_grad():
        # Iterate over images
        for i in range(num_images):
            im, name, shape, texture, shape_spec, texture_spec = dataset.__getitem__(i)
            im = im.unsqueeze(0)

            # Pass image into model
            embedding = softmax(penult_model(im)).numpy().squeeze()

            embedding_dict[name] = embedding.tolist()

    with open('embeddings/' + model_type + '_embeddings.json', 'w') as file:
        json.dump(embedding_dict, file)

    return embedding_dict


if __name__ == '__main__':
    """Passes images one at a time through a given model and stores/plots the results
    (the shape/texture of the image, the classification made, and whether or not
    the classifcation was a shape classification, a texture classification, or neither.)
    
    By default, the model is the SAYCAM-trained resnext model, and the dataset is the
    Geirhos ImageNet style-transfer dataset. These options can be changed when running
    this program in the terminal by using the -m and -d flags."""

    parser = argparse.ArgumentParser()
    parser.add_argument('-m', '--model', help='Example: saycam, resnet50', required=False, default='saycam')
    parser.add_argument('-v', '--verbose', help='Prints results.', required=False, action='store_true')
    parser.add_argument('-p', '--plot', help='Plots results.', required=False, action='store_true')
    parser.add_argument('-t', '--triplets', help='Obtains similarities for triplets of images.',
                        required=False, action='store_true')
    args = parser.parse_args()

    batch_size = 1
    shape_categories = sc.get_human_object_recognition_categories()  # list of 16 classes in the Geirhos style-transfer dataset
    shape_dir = 'stimuli-shape/style-transfer'
    texture_dir = 'stimuli-texture/style-transfer'
    plot = args.plot
    verbose = args.verbose
    t = args.triplets

    model_type = args.model  # 'saycam' or 'resnet50'

    if model_type == 'saycam':
        # Load Emin's pretrained SAYCAM model + ImageNet classifier from its .tar file
        model = models.resnext50_32x4d(pretrained=True)
        model.fc = nn.Linear(in_features=2048, out_features=1000, bias=True)
        model = nn.DataParallel(model)
        checkpoint = torch.load('models/fz_IN_resnext50_32x4d_augmentation_True_SAY_5_288.tar',
                                map_location=torch.device('cpu'))
        model.load_state_dict(checkpoint['model_state_dict'])
    elif model_type == 'saycamA':
        model = models.resnext50_32x4d(pretrained=False)
        model = nn.DataParallel(model)
        checkpoint = torch.load('models/TC-A.tar', map_location=torch.device('cpu'))
        model.load_state_dict(checkpoint['model_state_dict'], strict=False)
    elif model_type == 'saycamS':
        model = models.resnext50_32x4d(pretrained=False)
        model = nn.DataParallel(model)
        checkpoint = torch.load('models/TC-S.tar', map_location=torch.device('cpu'))
        model.load_state_dict(checkpoint['model_state_dict'], strict=False)
    elif model_type == 'saycamY':
        model = models.resnext50_32x4d(pretrained=False)
        model = nn.DataParallel(model)
        checkpoint = torch.load('models/TC-Y.tar', map_location=torch.device('cpu'))
        model.load_state_dict(checkpoint['model_state_dict'], strict=False)
    elif model_type == 'resnet50':
        model = models.resnet50(pretrained=True)

    # Put model in evaluation mode
    model.eval()

    # Create directories for results and plots
    try:
        os.mkdir('results/' + model_type)
    except FileExistsError:
        pass

    try:
        os.mkdir('figures/' + model_type)
    except FileExistsError:
        pass

    # Run simulations
    if t:
        try:
            os.mkdir('results/' + model_type + '/similarity')
        except FileExistsError:
            pass

        try:
            os.mkdir('figures/' + model_type + '/similarity')
        except FileExistsError:
            pass

        try:
            embeddings = json.load(open('embeddings/' + model_type + '_embeddings.json'))
        except FileNotFoundError:
            embeddings = get_embeddings(shape_dir, model, model_type)

        triplets(model_type, embeddings, verbose, shape_dir)
        #calculate_similarity_averages(model_type, shape_categories, plot)
        calculate_similarity_totals(model_type)
        #generate_fake_triplets(model_type, model, shape_dir)

        if plot:
            plot_similarity_histograms(model_type)

    else:
        shape_dict = dict.fromkeys(shape_categories)  # for storing the results
        shape_categories0 = [shape + '0' for shape in shape_categories]
        shape_dict0 = dict.fromkeys(shape_categories0)

        shape_spec_dict = dict.fromkeys(shape_categories)  # contains lists of specific textures for each shape
        for shape in shape_categories:
            shape_dict[shape] = shape_dict0.copy()
            shape_spec_dict[shape] = []

        # Load and process the images using my custom Geirhos style transfer dataset class
        style_transfer_dataset = GeirhosStyleTransferDataset(shape_dir, texture_dir)
        style_transfer_dataloader = DataLoader(style_transfer_dataset, batch_size=1, shuffle=False)
        if not os.path.isdir('stimuli-texture'):
            style_transfer_dataset.create_texture_dir('stimuli-shape/style-transfer', 'stimuli-texture')

        # Obtain ImageNet - Geirhos mapping
        mapping = probabilities_to_decision.ImageNetProbabilitiesTo16ClassesMapping()
        softmax = nn.Softmax(dim=1)
        softmax2 = nn.Softmax(dim=0)

        with torch.no_grad():
            # Pass images into the model one at a time
            for batch in style_transfer_dataloader:
                im, im_dir, shape, texture, shape_spec, texture_spec = batch

                # hack to extract vars
                im_dir = im_dir[0]
                shape = shape[0]
                texture = texture[0]
                shape_spec = shape_spec[0]
                texture_spec = texture_spec[0]

                output = model(im)
                soft_output = softmax(output).detach().numpy().squeeze()

                decision, class_values = mapping.probabilities_to_decision(soft_output)

                shape_idx = shape_categories.index(shape)
                texture_idx = shape_categories.index(texture)
                if class_values[shape_idx] > class_values[texture_idx]:
                    decision_idx = shape_idx
                else:
                    decision_idx = texture_idx
                decision_restricted = shape_categories[decision_idx]
                restricted_class_values = torch.Tensor([class_values[shape_idx], class_values[texture_idx]])
                restricted_class_values = softmax2(restricted_class_values)

                if verbose:
                    print('Decision for ' + im_dir + ': ' + decision)
                    print('\tRestricted decision: ' + decision_restricted)
                if plot:
                    plot_class_values(shape_categories, class_values, im_dir, shape, texture, model_type)

                shape_dict[shape][texture_spec + '0'] = [decision, class_values,
                                                    decision_restricted, restricted_class_values]
                shape_spec_dict[shape].append(texture_spec)

            csv_class_values(shape_dict, shape_categories, shape_spec_dict, 'results/' + model_type)
            calculate_totals(shape_categories, 'results/' + model_type, verbose)
            calculate_proportions('results/' + model_type, verbose)
