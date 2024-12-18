import torch
import numpy as np
import pandas as pd
import yaml

import utils
from torch_geometric.data import HeteroData
import torch_geometric.transforms as T
from torch_geometric.loader import LinkNeighborLoader


class MyHeteroData():
    def __init__(self, data_config):
        super().__init__()
        self.data_config = data_config
        self.ratings = pd.read_csv(data_config['ratings_path'])
        self.movies = pd.read_csv(data_config['movies_path'])
        self.links = pd.read_csv(data_config['links_path'])
        self.data = HeteroData()
    
    def preprocess_df(self):
        # map user and movie id to a unique id
        self.unique_user_id = self.ratings['userId'].unique()
        self.unique_user_id = pd.DataFrame(data={
            'userId': self.unique_user_id,
            'mappedID': pd.RangeIndex(len(self.unique_user_id)),
        })

        self.unique_movie_id = self.movies['movieId'].unique()
        self.unique_movie_id = pd.DataFrame(data={
            'movieId': self.unique_movie_id,
            'mappedID': pd.RangeIndex(len(self.unique_movie_id)),
        })

        self.ratings = pd.merge(self.ratings, self.unique_user_id, on='userId', how='left')
        self.ratings = pd.merge(self.ratings, self.unique_movie_id, on='movieId', how='left')
        self.ratings = pd.merge(self.ratings, self.links, on='movieId', how='left')
        self.ratings = self.ratings.drop(columns=['userId', 'movieId', 'timestamp', 'imdbId', 'tmdbId'])

        self.movies = pd.merge(self.movies, self.unique_movie_id, on='movieId', how='left')
    
    def create_user_movie_edges(self):
        self.data["user"].node_id = torch.arange(len(self.unique_user_id))
        self.data["movie"].node_id = torch.arange(len(self.unique_movie_id))

        self.num_users = self.data["user"].num_nodes = len(self.unique_user_id)
        self.num_movies = self.data["movie"].num_nodes = len(self.unique_movie_id)

        ratings_user_id = torch.from_numpy(self.ratings['mappedID_x'].values).to(torch.long)
        ratings_movie_id = torch.from_numpy(self.ratings['mappedID_y'].values).to(torch.long)
        # edge_index_user_to_movie = torch.stack([ratings_user_id, ratings_movie_id], dim=0)
        edge_index_user_to_movie = torch.stack([ratings_movie_id, ratings_user_id], dim=0)

        self.data["movie", "ratedby", "user"].edge_index = edge_index_user_to_movie.contiguous()
        rating = torch.from_numpy(self.ratings['rating'].values).to(torch.float)
        self.data["movie", "ratedby", "user"].rating = rating
        # print(self.data)

    def create_movie_genre_edges(self):
        all_genres = set(genre for genres in self.movies['genres'] for genre in genres.split('|'))

        self.data["genre"].node_id = torch.arange(len(all_genres))
        self.num_genres = self.data["genre"].num_nodes = len(all_genres)

        genre_to_id = {genre: idx for idx, genre in enumerate(all_genres)}
        
        edges = []
        for _, row in self.movies.iterrows():
            movie_id = row['mappedID']  # mappedID của movie
            genres = row['genres'].split('|')  # Tách genres
            for genre in genres:
                genre_id = genre_to_id[genre]  # mappedID của genre
                edges.append((genre_id, movie_id))  # Thêm cạnh
        edges_array = np.array(edges, dtype=np.int64)
        self.data['genre', 'of', 'movie'].edge_index = torch.from_numpy(edges_array.T).contiguous()

    def create_hetero_data(self):
        self.create_user_movie_edges()
        self.create_movie_genre_edges()
        # self.data = T.ToUndirected()(self.data)
        del self.ratings, self.movies, self.links 
    
    def split_data(self):
        transform = T.RandomLinkSplit(
                    num_val=self.data_config["val_ratio"],
                    num_test=self.data_config["test_ratio"],
                    add_negative_train_samples=False,
                    edge_types=("movie", "ratedby", "user"),
                    disjoint_train_ratio=0.2
                    # rev_edge_types=("movie", "rev_rates", "user"),
                )
        self.train_data, self.val_data, self.test_data = transform(self.data)

    def create_dataloader(self):
        batch_size = self.data_config['batch_size']
        self.trainloader = LinkNeighborLoader(
            self.train_data,
            batch_size = batch_size,
            shuffle = True,
            edge_label_index = (("movie", "ratedby", "user"), 
                                self.train_data["movie", "ratedby", "user"].edge_label_index), 
            edge_label = self.train_data["movie", "ratedby", "user"].edge_label,
            num_neighbors = self.data_config['num_neighbors'], 
        )
        self.valloader = LinkNeighborLoader(
            self.val_data,
            batch_size = batch_size,
            shuffle = False,
            edge_label_index = (("movie", "ratedby", "user"), 
                                self.val_data["movie", "ratedby", "user"].edge_label_index), 
            edge_label = self.val_data["movie", "ratedby", "user"].edge_label,  
            num_neighbors = self.data_config['num_neighbors'],  
        )
        self.testloader = LinkNeighborLoader(
            self.test_data,
            batch_size = batch_size,
            shuffle = False,
            edge_label_index = (("movie", "ratedby", "user"), 
                                self.test_data["movie", "ratedby", "user"].edge_label_index), 
            edge_label = self.test_data["movie", "ratedby", "user"].edge_label,  
            num_neighbors = self.data_config['num_neighbors'],  
        )
    
    def load_batches(self):
        for i, batch in enumerate(self.trainloader):
            print('-----------------')
            # print(batch)
            edge = batch["movie", "ratedby", "user"]
            edge_index, unique_edges, edge_label_index = utils.get_unlabel_label_edge(edge)
            print(edge_index.shape)
            print(unique_edges.shape)
            print(edge_label_index.shape)
            if i==5:
                break  

    def get_metadata(self):
        meta_tmp = self.data.metadata()
        meta_data = [{}, meta_tmp[1]]
        for key in meta_tmp[0]:
            meta_data[0][key] = self.data[key].num_nodes
        return meta_data
    
if __name__ == "__main__":
    # genres = ['Action', 'Adventure', 'Animation', 'Children', 'Comedy', 'Crime',
    #        'Documentary', 'Drama', 'Fantasy', 'Film-Noir', 'Horror, Musical',
    #        'Mystery', 'Romance', 'Sci-Fi', 'Thriller', 'War', 'Western', '(no genres listed)']
    
    with open('config.yaml') as f:
        config = yaml.safe_load(f)

    data_config = config['data']
    data = MyHeteroData(data_config)
    data.preprocess_df()
    data.create_hetero_data()
    data.split_data()
    data.create_dataloader()
    data.load_batches()
    # data.get_metadata()
