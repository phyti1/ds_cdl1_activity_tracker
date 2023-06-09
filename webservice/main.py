import pickle
import flask
import pandas as pd
import numpy as np
import os
import io
import json
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.ensemble import RandomForestClassifier
from tslearn.clustering import TimeSeriesKMeans
from sklearn.model_selection import GridSearchCV
from statistics import mode


from math import sin, cos, sqrt, atan2, radians


class CNN_2_bn(nn.Module):
    def __init__(self, num_classes, kernel_size=10, pool_size=3, padding=0, conv1_channels=10, conv2_channels=8, fc_linear_1=180, fc_linear_2=120, dropout=0):
        '''Convolutional Net class'''
        super(CNN_2_bn, self).__init__()
        self.conv1 = nn.Conv1d(
            in_channels=5, out_channels=conv1_channels, kernel_size=kernel_size, padding=padding)
        self.conv1_bn = nn.BatchNorm1d(conv1_channels)
        self.pool = nn.MaxPool1d(kernel_size=pool_size, stride=1)
        self.conv2 = nn.Conv1d(in_channels=conv1_channels,
                               out_channels=conv2_channels, kernel_size=kernel_size, padding=padding)
        self.conv2_bn = nn.BatchNorm1d(conv2_channels)
        self.fc1 = nn.Linear(in_features=conv2_channels *
                             68, out_features=fc_linear_1)
        self.fc1_bn = nn.BatchNorm1d(fc_linear_1)
        self.fc2 = nn.Linear(in_features=fc_linear_1, out_features=fc_linear_2)
        self.fc2_bn = nn.BatchNorm1d(fc_linear_2)
        self.fc3 = nn.Linear(in_features=fc_linear_2, out_features=num_classes)
        self.conv2_channels = conv2_channels
        self.dropout = nn.Dropout(p=dropout)

    def forward(self, x):
        '''
        Applies the forward pass
        Args:
            x (torch.tensor): input feature tensor
        Returns:
            x (torch.tensor): output tensor of size num_classes
        '''
        x = self.pool(F.relu(self.conv1_bn(self.conv1(x))))
        x = self.pool(F.relu(self.conv2_bn(self.conv2(x))))
        x = x.view(-1, self.conv2_channels*68)  # flatten tensor
        x = F.relu(self.fc1_bn(self.fc1(x)))
        x = self.dropout(x)
        x = F.relu(self.fc2_bn(self.fc2(x)))
        x = self.dropout(x)
        x = self.fc3(x)
        return x


class CNN_RS_FM(torch.nn.Module):
    def __init__(self, poolingFunction, bnFunction, activationFunction, kernel=None, stride=None, padding=None):
        super(CNN_RS_FM, self).__init__()

        # building network with specified number of layers, poolingFunction and bnFunction
        self.network = torch.nn.Sequential(
            torch.nn.Conv2d(in_channels=1, out_channels=16,
                            kernel_size=3, stride=1, padding=2),
            poolingFunction(kernel_size=2, stride=2, padding=1),
            activationFunction(),
            # bnFunction(num_features=16),

            torch.nn.Conv2d(in_channels=16, out_channels=32,
                            kernel_size=3, stride=1, padding=1),
            poolingFunction(kernel_size=2, stride=2, padding=1),
            activationFunction(),
            # bnFunction(num_features=32),

            torch.nn.Conv2d(in_channels=32, out_channels=64,
                            kernel_size=3, stride=1, padding=1),
            poolingFunction(kernel_size=2, stride=2, padding=1),
            activationFunction(),
            # bnFunction(num_features=64),
        )

        self.linStack = torch.nn.Sequential(
            torch.nn.Linear(in_features=1344, out_features=672),
            torch.nn.ReLU(),
            torch.nn.BatchNorm1d(num_features=672),

            torch.nn.Linear(in_features=672, out_features=336),
            torch.nn.ReLU(),
            torch.nn.BatchNorm1d(num_features=336),

            torch.nn.Linear(in_features=336, out_features=7),
        )

    def forward(self, x):
        x = self.network(x)
        x = torch.nn.Flatten(1)(x)
        x = self.linStack(x)
        return x


class ClusteringForest():
    def __init__(self, combineSensors=True, useEuclidCombineDistance=True, useStatisialParameters=False, useAllActivities=True, seed=420):
        self.combineOnSensor = combineSensors
        self.useEuclidCombineDistance = useEuclidCombineDistance
        self.useStatisialParameters = useStatisialParameters
        self.useAllActivities = useAllActivities
        self.seed = seed
        self.numberOfActivities = 7 if useAllActivities else 4
        self.numberOfSensors = 4 if combineSensors else 13
        self.models = None
        self.best_estimator = None

    def fit(self, X, y, maxIter=5, n_jobs=-1):
        # preprocess the data as needed
        preprocessedX = self.preprocess_X(X)

        self.fit_time_series_clustering(
            preprocessedX, maxIter=maxIter, n_jobs=n_jobs)
        distances = self.get_distances(preprocessedX)

        self.fit_random_forest(distances, y, n_jobs=n_jobs)
        return self

    def fit_time_series_clustering(self, preprocessedX, maxIter=5, n_jobs=-1):
        self.models = []
        for i in range(4):
            print("fitting model number {}".format(i))
            model = TimeSeriesKMeans(n_clusters=self.numberOfActivities, metric="dtw",
                                     max_iter=maxIter, random_state=self.seed, verbose=False, n_jobs=n_jobs)  # use number of activities as number of clusters
            model.fit(preprocessedX[:, :, i])
            self.models.append(model)

    def plot_centroids(self, names, size=(20, 20)):
        fig, ax = plt.subplots(self.numberOfActivities,
                               self.numberOfSensors, figsize=size)
        # model names
        # plot the centroids for each model
        for i in range(self.numberOfSensors):
            for j in range(self.numberOfActivities):
                ax[j, i].plot(self.models[i].cluster_centers_[j])

            # set the tiltle per column of subplot
            ax[0, i].set_title(names[i])

    def fit_random_forest(self, distances, y_train, n_jobs=-1):
        random_forest = RandomForestClassifier(random_state=self.seed)
        # create random forest grid search
        param_grid = {
            'n_estimators': [100, 300, 500],
            'max_depth': [15, 30, 45],
            'min_samples_split': [2, 6],
        }

        # run gridSerach
        grid_search = GridSearchCV(
            estimator=random_forest, param_grid=param_grid, cv=5, n_jobs=-n_jobs, verbose=5)
        grid_search.fit(distances, y_train)
        self.best_estimator = grid_search.best_estimator_

    def preprocess_X(self, X):
        if (self.combineOnSensor):
            if (self.useEuclidCombineDistance):
                pre_pros = self.combineAxisOnSensorsEuclidLength(X)
            else:
                pre_pros = self.combineAxisOnSensors(X)
        else:
            pre_pros = X
        return pre_pros

    def predict(self, X):
        if (self.best_estimator is None):
            return None

        prepros = self.preprocess_X(X)

        # stack distances horizontally
        distances = self.get_distances(prepros)

        return self.best_estimator.predict(distances)

    def get_distances(self, X):
        distances = []
        for i in range(len(self.models)):
            distances.append(self.models[i].transform(X[:, :, i]))

        distances = np.hstack(distances)
        if (self.useStatisialParameters):
            # stack statistical parameters to distances
            distances = np.hstack(
                (distances, ClusteringForest.calclateStatical_measurements(X)))
        return distances

    def score(self, X, y):
        if (self.best_estimator is None):
            return None
        else:
            prepros = self.preprocess_X(X)
            distances = self.get_distances(prepros)
            return self.best_estimator.score(distances, y)

    @staticmethod
    def calclateStatical_measurements(X):
        # calculate mean, min, max and std for each sensor time series
        statsMeasurements = []
        for j in range(X.shape[0]):  # for each sample
            statsMeasurements.append([])  # add empty list for each sample
            for i in range(X.shape[-1]):  # for each sensor
                statsMeasurements[j].append(np.mean(X[j, :, i]))
                statsMeasurements[j].append(np.min(X[j, :, i]))
                statsMeasurements[j].append(np.max(X[j, :, i]))
                statsMeasurements[j].append(np.std(X[j, :, i]))
        return statsMeasurements

    @staticmethod
    def combineAxisOnSensors(input):
        s, n, f = input.shape
        output = np.zeros((s, n, 4))
        # for each sample, sum rows 0-2
        output[:, :, 0] = input[:, :, 0] + input[:, :, 1] + input[:, :, 2]
        # for each sample, sum rows 3-5
        output[:, :, 1] = input[:, :, 3] + input[:, :, 4] + input[:, :, 5]
        # for each sample, sum rows 6-8
        output[:, :, 2] = input[:, :, 6] + input[:, :, 7] + input[:, :, 8]
        # for each sample, sum rows 9-12
        output[:, :, 3] = input[:, :, 9] + input[:, :, 10] + \
            input[:, :, 11] + input[:, :, 12]
        return output

    @staticmethod
    def combineAxisOnSensorsEuclidLength(input):
        s, n, _ = input.shape
        output = np.zeros((s, n, 4))
        # for each sample, sum rows 0-2
        output[:, :, 0] = np.sqrt(
            input[:, :, 0] ** 2 + input[:, :, 1] ** 2 + input[:, :, 2] ** 2)
        # for each sample, sum rows 3-5
        output[:, :, 1] = np.sqrt(
            input[:, :, 3] ** 2 + input[:, :, 4] ** 2 + input[:, :, 5] ** 2)
        # for each sample, sum rows 6-8
        output[:, :, 2] = np.sqrt(
            input[:, :, 6] ** 2 + input[:, :, 7] ** 2 + input[:, :, 8] ** 2)
        # for each sample, sum rows 9-12
        output[:, :, 3] = np.sqrt(input[:, :, 9] ** 2 + input[:, :, 10]
                                  ** 2 + input[:, :, 11] ** 2 + input[:, :, 12] ** 2)
        return output


app = flask.Flask(__name__)
port = int(9099)


# print current path
print(os.getcwd())
model_path = "./Analyse/webservice/models/"
if(not os.path.exists(model_path)):
    model_path = "./webservice/models/"
    if(not os.path.exists(model_path)):
        model_path = "./test/cdl1/models/"
        if(not os.path.exists(model_path)):
            raise Exception("Model path does not exist")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

models = dict()
for root, dirs, files in os.walk(model_path):
    for file in files:
        model_name = file.split(".")[0]
        if file.endswith(".pkl"):
            # print(model_name)
            models[model_name] = pickle.load(open(model_path + file, 'rb'))

        elif file == "cnn_RS_FM.pt":
            models[model_name] = CNN_RS_FM(
                poolingFunction=torch.nn.MaxPool2d, bnFunction=torch.nn.BatchNorm2d, activationFunction=torch.nn.ReLU)
            models[model_name].load_state_dict(
                torch.load(model_path + file, map_location=device))

        elif file.endswith(".pt"):
            models[model_name] = torch.load(
                model_path + file, map_location=device)


@app.route('/predict', methods=['POST'])
def predict():
    try:
        # strict = False because of invalid control character in csv
        post_content = json.loads(
            flask.request.get_data(as_text=True), strict=False)
        csv_test = post_content["data"]
        buf = io.StringIO()
        buf.write(csv_test)
        # reset buffer read position
        buf.seek(0)
        df_test = pd.read_csv(buf, sep=',', header=None)

        df_test = prep_allgemein(df_test)

        if(post_content["model"] == "SPR_RandomForest"):
            df_test = groupby_gruppe_bienli(process_data_group_bienli(df_test))
            # TODO Modell mit 25 features trainieren
            prediction = models['rf_allprop'].predict(
                df_test.iloc[-1, 2:].values.reshape((1, -1)))

        if(post_content["model"] == "SPR_CNN1"):
            df_test = process_data_group_bienli(df_test)
            # normalize scales with min max from training
            df_test = normalize_scales(df_test)
            tensors = transform_to_tensors(df_test)  # create tensors
            prediction = [predict_with_cnn_1(
                tensors, model=models['2022-06-04-14-36-07'], num_classes=7)]

        if(post_content["model"] == "SPR_CNN2"):
            prediction = models['cnn2_XYZ'].predict(df_test)

        if(post_content["model"] == "RF_cnn"):
            # data preprocessing
            data = df_test
            data = prep_RS_FM(data)
            data = data.unsqueeze(1).float().to(device)
            # predictions
            models["cnn_RS_FM"].eval()
            pred = models["cnn_RS_FM"](data)
            pred = torch.argmax(pred, dim=1)
            pred = torch.argmax(pred).item()
            labels = ['Sitting', 'Transport', 'Bicycling',
                      'Walking', 'Elevatoring', 'Jogging', 'Stairway']
            prediction = [labels[pred]]

        if(post_content["model"] == "RF_Cluster"):
            # data preprocessing
            data = df_test
            data = prep_RS_FM(data)
            pred = mode(models["RF_Cluster"].predict(data))
            labels = ['Sitting', 'Transport', 'Bicycling',
                      'Walking', 'Elevatoring', 'Jogging', 'Stairway']
            prediction = [labels[pred]]

        response = prediction[0]

        return response
    except Exception as e:
        return {'error': str(e)}


def prep_RS_FM(df, samplesize: int = 43):
    '''
    Prepares data for Group RS_FM

    - removes all columns but ['acc_x','acc_y','acc_z','mag_x','mag_y','mag_z','gyr_x','gyr_y','gyr_z','ori_x','ori_y','ori_z','ori_w']
    - turns pd.DataFrame into np.array
    - splits array into samples of size samplesize
    - turns list with splits into array
    - checks shape
    - turns array into tensor

    '''
    # remove unnecessary columns
    df.columns = ['time', 'name', 'activity', 'acc_x', 'acc_y', 'acc_z', 'mag_x', 'mag_y',
                  'mag_z', 'gyr_x', 'gyr_y', 'gyr_z', 'ori_x', 'ori_y', 'ori_z', 'ori_w', 'lat', 'long']
    # df.loc[:, 'time'] = pd.to_datetime(df.loc[:, 'time'] + "000") # format="%d.%m.%Y %H:%M.%S.%f"
    df = df[['acc_x', 'acc_y', 'acc_z', 'mag_x', 'mag_y', 'mag_z',
             'gyr_x', 'gyr_y', 'gyr_z', 'ori_x', 'ori_y', 'ori_z', 'ori_w']]
    arr = df.values

    # split into samples
    split = arr.shape[0] // samplesize
    stop = samplesize * split
    arr = arr[:stop, :]
    arr = np.array(np.array_split(arr, split))

    # assert shape
    assert arr.shape == (split, samplesize, 13)

    # turn into tensor
    arr = torch.from_numpy(arr)  # .to(device)

    return arr


def prep_allgemein(df):
    df.columns = ['time', 'name', 'activity', 'acc_x', 'acc_y', 'acc_z', 'mag_x', 'mag_y',
                  'mag_z', 'gyr_x', 'gyr_y', 'gyr_z', 'ori_x', 'ori_y', 'ori_z', 'ori_w', 'lat', 'long']
    df.loc[:, 'time'] = pd.to_datetime(
        df.loc[:, 'time'] + "000")  # format="%d.%m.%Y %H:%M.%S.%f"
    return df


def process_data_group_bienli(df):

    df['time'] = pd.to_datetime(df['time'])

    # accelometer
    df['acc'] = (df.iloc[:, 3:6] + 10).sum(axis=1)
    # magnetometer
    df['mag'] = (df.iloc[:, 6:9] + 2000).sum(axis=1)
    # gyroscope
    df['gyr'] = (df.iloc[:, 9:12] + 30).sum(axis=1)
    # orientation
    df['ori'] = (df.iloc[:, 12:16] + 1).sum(axis=1)

    # drop old axis columns
    df.drop(columns=df.columns[3:16], inplace=True)

    gps_diff = (df.loc[:, ["lat", "long"]] -
                df.loc[:, ["lat", "long"]].shift(-1))
    gps_diff.replace(0, np.NAN, inplace=True)
    gps_diff[abs(gps_diff) > 1e-3] = np.NAN

    df["gps_differs"] = (df[["lat", "long"]] -
                         df[["lat", "long"]].shift(-1)).sum(axis=1).astype(bool)

    dists = []
    gps_differs_index = df.loc[df["gps_differs"] == True, "gps_differs"].index
    for i in range(gps_differs_index.shape[0]):
        dists.append(get_distance(df.loc[gps_differs_index[i], [
                     "lat", "long"]], df.loc[gps_differs_index[i]+1, ["lat", "long"]]))

    # calculate time differences
    time_differences = pd.Series(
        df.loc[gps_differs_index, "time"].values - df.loc[gps_differs_index.insert(0, 0)[:-1], "time"].values)
    time_diff_secs = time_differences.dt.total_seconds()

    time_diff_secs.loc[(time_diff_secs > 10) | (time_diff_secs < 0)] = 0.0

    # m/s ausrechen, in km/h umrechnen
    kmh = (dists / time_diff_secs * 3.6)
    kmh[kmh == np.inf] = np.nan
    kmh[kmh > 120] = np.nan
    kmh[kmh < 1] = 0

    # geschwindigkeit inseriteren
    df.loc[gps_differs_index, "kmh"] = kmh
    df.drop(columns=["lat", "long", "gps_differs"], inplace=True)

    df["kmh"] = df["kmh"].ffill().fillna(0)
    return df


def groupby_gruppe_bienli(df):

    old_time = pd.to_datetime("01.01.2022 00:00:00")

    df[f"5s_index0"] = ((df["time"] - old_time).dt.total_seconds() // 5)
    # for i  in range(1, 5):
    #   df[f"5s_index{i}"] = df[f"5s_index0"].shift(round(i * 90/5))

    # inplace sifted values at the beginning
    df.fillna(0, inplace=True)

    groups = ["5s_index0"]  # ["5s_index", "name", "activity"]
    df_wind = df.groupby(groups)
    # print group count
    print(df_wind.size().count())

    target = df_wind[["name", "activity", "5s_index0"]
                     ].first().reset_index(drop=True)

    df_agg = df_wind[df.columns[3:8]].agg(
        ["var", "mean", "median", "min", "max"]).reset_index()

    # flatten mutli index columns
    df_agg.columns = ['_'.join(column) for column in df_agg.columns]

    df_agg = df_agg.drop(columns=["5s_index0_"])
    return df_agg


def get_distance(point1, point2):
    if(point1[0] == point2[0] and point1[1] == point2[1]):
        return 0.0

    R = 6370
    lat1 = radians(point1[0])  # insert value
    lon1 = radians(point1[1])
    lat2 = radians(point2[0])
    lon2 = radians(point2[1])

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c
    return distance


def transform_to_tensors(df, window_size=90, stride=10):
    '''
    Transforms the dataframe into a 3d tensor of shape (X, 5, 90)
    Args:
        df (pandas DataFrame): dataframe with sensordata
    Returns:
        tensors (torch tensor)
    '''
    # create array
    arr_2d = df[["acc", "mag", "gyr", "ori", "kmh"]].values

    # slide over array to create moving windows
    arr_3d = np.lib.stride_tricks.sliding_window_view(
        arr_2d, window_size, 0)[::stride]

    # create tensors
    tensors = torch.tensor(arr_3d).to(device)
    tensors = tensors.float()
    return tensors


def predict_with_cnn_1(tensors, model, num_classes=7):
    '''
    Predicts an activity using a cnn model from a tensor of sensordata "acc", "mag", "gyr", "ori", "kmh"
    Args:
        tensors (torch tensor): the sensordaa as 3d tensor of format (X, 5, 90)
        model (str): the cnn model
        num_classes (int): 4 or 7 depeding on the number of classes to predict
    Returns:
        String prediction
    '''
    itos_7 = {
        0: "Bicycling",
        1: "Elevatoring",
        2: "Jogging",
        3: "Sitting",
        4: "Stairway",
        5: "Transport",
        6: "Walking"}

    # set model to eval mode
    model.eval()
    # predict
    with torch.no_grad():
        output = model(tensors)
        pred = torch.max(output, 1)
        labels = pred.indices
        labels_numpy = labels.cpu().numpy()
        counts = np.bincount(labels_numpy)
        if num_classes == 7:
            return itos_7[np.argmax(counts)]


def normalize_scales(df):
    df = df.copy()
    x = df[["acc", "mag", "gyr", "ori", "kmh"]]
    scale_max = [4.42877550e+01, 8.81122530e+03,
                 1.16565825e+02, 5.99909750e+00, 1.18619879e+02]
    scale_min = [1.87147230e+01, 3.29054710e+03,
                 5.16912100e+01, 2.27530129e+00, 0.00000000e+00]
    for i, col in enumerate(x.columns):
        x[col] = (x[col] - scale_min[i]) / (scale_max[i] - scale_min[i])
    return x


if __name__ == '__main__':
    # host on every ip avaliable
    app.run(host="0.0.0.0", port=port)
