# coding=utf-8

import os
import shutil

import numpy as np
import tensorflow as tf
from scipy.sparse import coo_matrix


#######################################################################################################################
## GRAPH OBJECT CLASS #################################################################################################
#######################################################################################################################
class GraphObject:
    ## CONSTRUCTORS METHODS ###########################################################################################
    def __init__(self, nodes, arcs, targets,
                 problem_based: str = 'n',
                 set_mask=None,
                 output_mask=None,
                 sample_weights=1,
                 NodeGraph=None,
                 ArcNode=None,
                 aggregation_mode: str = 'average'):
        """ CONSTRUCTOR METHOD

        :param arcs: Ordered Arcs Matrix where arcs[i] = [ID Node From | ID NodeTo | Arc Label].
        :param nodes: Ordered Nodes Matrix where nodes[i] = [Node Label].
        :param targets: Targets Array with shape (Num of targeted example [nodes or arcs], dim_target example).
        :param problem_based: (str) define the problem on which graph is used: 'a' arcs-based, 'g' graph-based, 'n' node-based.
        :param set_mask: Array of {0,1} to define arcs/nodes belonging to a set, when dataset == single GraphObject.
        :param output_mask: Array of {0,1} to define the sub-set of arcs/nodes whose target is known.
        :param sample_weights: target sample weight for loss computation. It can be int, float or numpy.array of ints or floats
            > If int, all targets are weighted as sample_weights * ones.
            > If numpy.array, len(sample_weights) and targets.shape[0] must agree.
        :param NodeGraph: Matrix (nodes.shape[0],{Num graphs or 1}) used only when problem_based=='g'.
        :param ArcNode: Matrix of shape (num_of_arcs, num_of_nodes) s.t. A[i,j]=value if arc[i,2]==node[j].
        :param aggregation_mode: (str) It defines the aggregation mode for the incoming message of a node using ArcNode and Adjacency:
            > 'average': elem(matrix)={0-1} -> matmul(m,A) gives the average of incoming messages, s.t. sum(A[:,i])=1;
            > 'normalized': elem(matrix)={0-1} -> matmul(m,A) gives the normalized message wrt the total number of g.nodes;
            > 'sum': elem(matrix)={0,1} -> matmul(m,A) gives the total sum of incoming messages. In this case Adjacency
        """
        self.dtype = tf.keras.backend.floatx()

        # store arcs, nodes, targets
        self.nodes = nodes.astype(self.dtype)
        self.arcs = arcs.astype(self.dtype)
        self.targets = targets.astype(self.dtype)
        self.sample_weights = sample_weights * np.ones(self.targets.shape[0])

        # store dimensions: first two columns of arcs contain nodes indices
        self.DIM_NODE_LABEL = nodes.shape[1]
        self.DIM_ARC_LABEL = arcs.shape[1] - 2
        self.DIM_TARGET = targets.shape[1]

        # setting the problem type: node, arcs or graph based + check existence of passed parameters in keys
        lenMask = {'n': nodes.shape[0], 'a': arcs.shape[0], 'g': nodes.shape[0]}

        # build set_mask, for a dataset composed of only a single graph: its nodes have to be divided in Tr, Va and Te
        self.set_mask = np.ones(lenMask[problem_based], dtype=bool) if set_mask is None else set_mask.astype(bool)
        # build output_mask
        self.output_mask = np.ones(len(self.set_mask), dtype=bool) if output_mask is None else output_mask.astype(bool)

        # check lengths: output_mask must be as long as set_mask
        if len(self.set_mask) != len(self.output_mask): raise ValueError('Error - len(<set_mask>) != len(<output_mask>)')

        # nodes and arcs aggregation
        if aggregation_mode not in ['average', 'normalized', 'sum']: raise ValueError("ERROR: Unknown aggregation mode")
        self.aggregation_mode = aggregation_mode

        # build ArcNode matrix or acquire it from input
        self.ArcNode = self.buildArcNode() if ArcNode is None else coo_matrix(ArcNode, dtype=self.dtype)

        # build Adjancency Matrix. Note that it can be an Aggregated Version of the 'normal' Adjacency Matrix (with only 0 and 1)
        self.Adjacency = self.buildAdjacency()

        # build node_graph conversion matrix
        self.NodeGraph = self.buildNodeGraph(problem_based) if NodeGraph is None else coo_matrix(NodeGraph, dtype=self.dtype)

    # -----------------------------------------------------------------------------------------------------------------
    def copy(self):
        """ COPY METHOD

        :return: a Deep Copy of the GraphObject instance.
        """
        return GraphObject(arcs=self.getArcs(), nodes=self.getNodes(), targets=self.getTargets(), set_mask=self.getSetMask(),
                           output_mask=self.getOutputMask(), sample_weights=self.getSampleWeights(), NodeGraph=self.getNodeGraph(),
                           aggregation_mode=self.aggregation_mode)

    # -----------------------------------------------------------------------------------------------------------------
    def buildAdjacency(self):
        """ Build 'Aggregated' Adjacency Matrix ADJ, s.t. ADJ[i,j]=value if edge (i,j) exists in graph edges set.
        value is set by self.aggregation_mode: 'sum':1, 'normalized':1/self.nodes.shape[0], 'average':1/number_of_neighbors """
        values = self.ArcNode.data
        indices = zip(*self.arcs[:, :2].astype(int))
        return coo_matrix((values, indices), shape=(self.nodes.shape[0], self.nodes.shape[0]), dtype=self.dtype)

    # -----------------------------------------------------------------------------------------------------------------
    def buildArcNode(self):
        """ Build ArcNode Matrix A of shape (number_of_arcs, number_of_nodes) where A[i,j]=value if arc[i,2]==node[j].
        Compute the matmul(m:=message,A) to get the incoming message on each node.
        :return: sparse ArcNode Matrix, for memory efficiency.
        :raise: Error if <aggregation_mode> is not in ['average','sum','normalized'].
        """

        col = self.arcs[:, 1]  # column indices of A are located in the second column of the arcs tensor
        row = np.arange(0, len(col))  # arc id (from 0 to number of arcs)

        # sum node aggregation - incoming message as sum of neighbors states and labels
        values_vector = np.ones(len(col))

        # normalized node aggregation - incoming message as sum of neighbors states and labels divided by the number of nodes in the graph
        if self.aggregation_mode == 'normalized':
            values_vector = values_vector * float(1 / len(col))

        # average node aggregation - incoming message as average of neighbors states and labels
        elif self.aggregation_mode == 'average':
            val, col_index, destination_node_counts = np.unique(col, return_inverse=True, return_counts=True)
            values_vector = values_vector / destination_node_counts[col_index]

        # isolated nodes correction: if nodes[i] is isolated, then ArcNode[:,i]=0, to maintain nodes ordering
        return coo_matrix((values_vector, (row, col)), shape=(self.arcs.shape[0], self.nodes.shape[0]), dtype=self.dtype)

    # -----------------------------------------------------------------------------------------------------------------
    def setAggregation(self, aggregation_mode: str):
        """ Set ArcNode values for the specified :param aggregation_mode: """
        if aggregation_mode not in ['average', 'normalized', 'sum']: raise ValueError("ERROR: Unknown aggregation mode")
        self.aggregation_mode = aggregation_mode
        self.ArcNode = self.buildArcNode()
        self.Adjacency = self.buildAdjacency()

    # -----------------------------------------------------------------------------------------------------------------
    def buildNodeGraph(self, problem_based: str):
        """ Build Node-Graph Aggregation Matrix, to transform a node-based problem in a graph-based one.
        nodegraph != None only if problem_based == 'g': It has dimensions (nodes.shape[0], 1) for a single graph, 
        or (nodes.shape[0], Num graphs) for a graph containing 2+ graphs, built by merging the single graphs into a bigger one,
        such that after the node-graph aggregation process gnn can compute (Num graphs, targets.shape[1]) as output.
        It's normalized wrt the number of nodes whose output is computed, i.e. the number of ones in output_mask.
        :return: nodegraph matrix if :param problem_based: is 'g' else None, as nodegraph is used in graph-based problems.
        """
        nodegraph = coo_matrix([], dtype=self.dtype)
        if problem_based == 'g':
            data = np.ones((self.nodes.shape[0], 1), dtype=np.float32) * 1 / self.nodes.shape[0]
            nodegraph = coo_matrix(data, dtype=self.dtype)
        return nodegraph

    ## REPRESENTATION METHODs #########################################################################################
    def __repr__(self):
        set_mask_type = 'all' if np.all(self.set_mask) else 'mixed'
        return f"graph(n={self.nodes.shape[0]}, a={self.arcs.shape[0]}, " \
               f"ndim={self.DIM_NODE_LABEL}, adim={self.DIM_ARC_LABEL}, tdim={self.DIM_TARGET}, " \
               f"set={set_mask_type}, mode={self.aggregation_mode})"

    # -----------------------------------------------------------------------------------------------------------------
    def __str__(self):
        return self.__repr__()

    ## SAVER METHODs ##################################################################################################
    def save(self, graph_folder_path: str) -> None:
        """ save graph in folder. All attributes are saved in numpy .npy files.

        :param graph_folder_path: (str) folder path in which graph is saved.
        """
        GraphObject.save_graph(graph_folder_path, self)

    # -----------------------------------------------------------------------------------------------------------------
    def savetxt(self, graph_folder_path: str, format: str = '%.10g') -> None:
        """ save graph in folder. All attributes are saved in textual .txt files.

        :param graph_folder_path: (str) folder path in which graph is saved.
        """
        GraphObject.save_txt(graph_folder_path, self, format)

    ## GETTERS ########################################################################################################
    def getArcs(self):
        return self.arcs.copy()

    def getNodes(self):
        return self.nodes.copy()

    def getTargets(self):
        return self.targets.copy()

    def getSetMask(self):
        return self.set_mask.copy()

    def getOutputMask(self):
        return self.output_mask.copy()

    def getAdjacency(self):
        return self.Adjacency.copy()

    def getArcNode(self):
        return self.ArcNode.copy()

    def getNodeGraph(self):
        return self.NodeGraph.copy()

    def getSampleWeights(self):
        return self.sample_weights.copy()

    ## CLASS METHODs ##################################################################################################
    @classmethod
    def save_graph(self, graph_folder_path: str, g, *args, **kwargs):
        """ Save a graph to a directory, creating txt files referring to all attributes of graph g
        Note that graph_folder_path will contain ONLY a single graph g. If folder is not empty, it is removed and re-made
        Remind that dataset folder contains one folder for each graph.

        :param graph_folder_path: new directory for saving the graph. 
        :param g: graph of type GraphObject to be saved.
        :param *args: all args of numpy.save function.
        :param **kwargs: all kwargs of numpy.save function.
        """
        # check folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        if os.path.exists(graph_folder_path): shutil.rmtree(graph_folder_path)
        os.makedirs(graph_folder_path)

        # save everything
        np.save(graph_folder_path + 'arcs.npy', g.arcs, *args, **kwargs)
        np.save(graph_folder_path + 'nodes.npy', g.nodes, *args, **kwargs)
        np.save(graph_folder_path + 'targets.npy', g.targets, *args, **kwargs)
        if not all(g.set_mask): np.save(graph_folder_path + 'set_mask.npy', g.set_mask, *args, **kwargs)
        if not all(g.output_mask): np.save(graph_folder_path + 'output_mask.npy', g.output_mask, *args, **kwargs)
        if np.any(g.sample_weights != 1): np.save(graph_folder_path + 'sample_weights.npy', g.sample_weights, *args, **kwargs)
        if all(g.NodeGraph.shape) and g.targets.shape[0] > 1:
            nodegraph = np.stack([g.NodeGraph.data, g.NodeGraph.row, g.NodeGraph.col])
            np.save(graph_folder_path + 'NodeGraph.npy', nodegraph, *args, **kwargs)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def save_txt(self, graph_folder_path: str, g, fmt: str = '%.10g', *args, **kwargs):
        """ Save a graph to a directory, creating txt files referring to all attributes of graph g
        Note that graph_folder_path will contain ONLY a single graph g. If folder is not empty, it is removed and re-made.
        Remind that dataset folder contains one folder for each graph.

        :param graph_folder_path: new directory for saving the graph.
        :param g: graph of type GraphObject to be saved.
        :param fmt: param passed to np.savetxt function.
        :param *args: all args of numpy.savetxt function.
        :param **kwargs: all kwargs of numpy.savetxt function.
        """
        # check folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        if os.path.exists(graph_folder_path): shutil.rmtree(graph_folder_path)
        os.makedirs(graph_folder_path)

        # save everything
        np.savetxt(graph_folder_path + 'arcs.txt', g.arcs, *args, fmt=fmt, **kwargs)
        np.savetxt(graph_folder_path + 'nodes.txt', g.nodes, *args,  fmt=fmt, **kwargs)
        np.savetxt(graph_folder_path + 'targets.txt', g.targets, *args, fmt=fmt, **kwargs)
        if not all(g.set_mask): np.savetxt(graph_folder_path + 'set_mask.txt', g.set_mask, *args, fmt=fmt, **kwargs)
        if not all(g.output_mask): np.savetxt(graph_folder_path + 'output_mask.txt', g.output_mask, *args, fmt=fmt, **kwargs)
        if np.any(g.sample_weights != 1): np.savetxt(graph_folder_path + 'sample_weights.txt', g.sample_weights, *args, fmt=fmt, **kwargs)
        if all(g.NodeGraph.shape) and g.targets.shape[0] > 1:
            nodegraph = np.stack([g.NodeGraph.data, g.NodeGraph.row, g.NodeGraph.col])
            np.savetxt(graph_folder_path + 'NodeGraph.txt', nodegraph, *args, fmt=fmt, **kwargs)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def load(self, graph_folder_path: str, problem_based: str, aggregation_mode: str, *args, **kwargs):
        """ Load a graph from a directory which contains at least 3 numpy files referring to nodes, arcs and targets

        :param graph_folder_path: directory containing at least 3 files: 'nodes.npy', 'arcs.npy' and 'targets.npy'
            > other possible files: 'NodeGraph.npy','output_mask.npy' and 'set_mask.npy'. No other files required!
        :param aggregation_mode: node aggregation mode: 'average','sum','normalized'. Go to BuildArcNode for details
        :param problem_based: (str) : 'n'-nodeBased; 'a'-arcBased; 'g'-graphBased
            > NOTE  For graph_based problems, file 'NodeGraph.npy' must be present in folder
                    NodeGraph has shape (nodes, 3) s.t. in coo_matrix -> NodeGraph[0,:]==data, NodeGraph[1:,:]==indices for data
        :param *args: all args of numpy.load function.
        :param **kwargs: all kwargs of numpy.load function.
        :return: GraphObject described by files in <graph_folder_path> folder
        """
        # load all the files inside <graph_folder_path> folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        files = os.listdir(graph_folder_path)
        keys = [i.rsplit('.')[0] for i in files] + ['problem_based', 'aggregation_mode']
        vals = [np.load(graph_folder_path + i, *args, **kwargs) for i in files] + [problem_based, aggregation_mode]

        # create a dictionary with parameters and values to be passed to constructor and return GraphObject
        params = dict(zip(keys, vals))

        # Translate Nodegraph from (length, 3) to coo matrix.
        if 'NodeGraph' in params: params['NodeGraph'] = coo_matrix((params['NodeGraph'][0,:], params['NodeGraph'][1:,:].astype(int)))

        return self(**params)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def load_txt(self, graph_folder_path: str, problem_based: str, aggregation_mode: str, *args, **kwargs):
        """ Load a graph from a directory which contains at least 3 txt files referring to nodes, arcs and targets

        :param graph_folder_path: directory containing at least 3 files: 'nodes.txt', 'arcs.txt' and 'targets.txt'
            > other possible files: 'NodeGraph.txt','output_mask.txt' and 'set_mask.txt'. No other files required!
        :param problem_based: (str) : 'n'-nodeBased; 'a'-arcBased; 'g'-graphBased
            > NOTE  For graph_based problems, file 'NodeGraph.txt' must to be present in folder
                    NodeGraph has shape (nodes, 3) s.t. in coo_matrix -> NodeGraph[0,:]==data, NodeGraph[1:,:]==indices for data
        :param aggregation_mode: node aggregation mode: 'average','sum','normalized'. Go to BuildArcNode for details
        :param *args: all args of numpy.loadtxt function.
        :param **kwargs: all kwargs of numpy.loadtxt function.
        :return: GraphObject described by files in <graph_folder_path> folder
        """
        # load all the files inside <graph_folder_path> folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        files = os.listdir(graph_folder_path)
        keys = [i.rsplit('.')[0] for i in files] + ['problem_based', 'aggregation_mode']
        vals = [np.loadtxt(graph_folder_path + i, ndmin=2, *args, **kwargs) for i in files] + [problem_based, aggregation_mode]

        # create a dictionary with parameters and values to be passed to constructor and return GraphObject
        params = dict(zip(keys, vals))

        # Translate Nodegraph from (length, 3) to coo matrix.
        if 'NodeGraph' in params: params['NodeGraph'] = coo_matrix((params['NodeGraph'][0,:], params['NodeGraph'][1:,:].astype(int)))

        return self(**params)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def merge(self, glist: list, problem_based: str, aggregation_mode: str, dtype='float32'):
        """ Method to merge graphs: it takes in input a list of graphs and returns them as a single graph

        :param glist: list of GraphObjects
            > NOTE if problem_based=='g', new NodeGraph will have dimension (Num nodes, Num graphs) else None
        :param aggregation_mode: str, node aggregation mode for new GraphObject, go to buildArcNode for details
        :return: a new GraphObject containing all the information (nodes, arcs, targets, etc) in glist
        """
        # check glist parameter: others parameter are in constructor
        if not (type(glist) == list and all(isinstance(x, (GraphObject, str)) for x in glist)):
            raise TypeError('type of param <glist> must be list of str \'path-like\' or GraphObjects')

        get_data = lambda x: [(i.getNodes(), i.nodes.shape[0], i.getArcs(), i.getTargets(), i.getSetMask(), i.getOutputMask(),
                               i.getSampleWeights(), i.getNodeGraph()) for i in x]
        nodes, nodes_lens, arcs, targets, set_mask, output_mask, sample_weights, nodegraph_list = zip(*get_data(glist))

        # get single matrices for new graph
        for i, elem in enumerate(arcs): elem[:, :2] += sum(nodes_lens[:i])
        arcs = np.concatenate(arcs, axis=0, dtype=dtype)
        nodes = np.concatenate(nodes, axis=0, dtype=dtype)
        targets = np.concatenate(targets, axis=0, dtype=dtype)
        set_mask = np.concatenate(set_mask, axis=0, dtype=dtype)
        output_mask = np.concatenate(output_mask, axis=0, dtype=dtype)
        sample_weights = np.concatenate(sample_weights, axis=0, dtype=dtype)

        #nodegraph = None
        #if problem_based == 'g':
        from scipy.sparse import block_diag
        nodegraph = block_diag(nodegraph_list, dtype=dtype)

        # resulting GraphObject
        return self(arcs=arcs, nodes=nodes, targets=targets, problem_based=problem_based, set_mask=set_mask, output_mask=output_mask,
                    sample_weights=sample_weights, NodeGraph=nodegraph, aggregation_mode=aggregation_mode)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def fromGraphTensor(self, g, problem_based: str):
        nodegraph = g.NodeGraph.numpy() if problem_based == 'g' else None
        return self(arcs=g.arcs.numpy(), nodes=g.nodes.numpy(), targets=g.targets.numpy(),
                    set_mask=g.set_mask.numpy(), output_mask=g.output_mask.numpy(), sample_weights=g.sample_weights.numpy(),
                    NodeGraph=nodegraph, aggregation_mode=g.aggregation_mode, problem_based=problem_based)



#######################################################################################################################
## GRAPH OBJECT CLASS #################################################################################################
#######################################################################################################################
class GraphTensor:
    ## CONSTRUCTORS METHODS ###########################################################################################
    def __init__(self, nodes, arcs, targets, set_mask, output_mask, sample_weights, Adjacency, ArcNode, NodeGraph, aggregation_mode):
        dtype = tf.keras.backend.floatx()

        # store dimensions: first two columns of arcs contain nodes indices
        self.DIM_NODE_LABEL = nodes.shape[1]
        self.DIM_ARC_LABEL = arcs.shape[1] - 2
        self.DIM_TARGET = targets.shape[1]

        self.nodes = tf.constant(nodes, dtype=dtype)
        self.arcs = tf.constant(arcs, dtype=dtype)
        self.targets = tf.constant(targets, dtype=dtype)
        self.sample_weights = tf.constant(sample_weights, dtype=dtype)
        self.set_mask = tf.constant(set_mask, dtype=bool)
        self.output_mask = tf.constant(output_mask, dtype=bool)
        self.aggregation_mode = aggregation_mode

        #self.NodeGraph = tf.zeros((0,0), dtype=dtype)
        #if NodeGraph is not None: self.NodeGraph = tf.constant(NodeGraph, dtype=dtype)


        # Adjacency and ArcNode in GraphTensor MUST BE already transposed!
        self.Adjacency = tf.sparse.SparseTensor.from_value(Adjacency)
        self.ArcNode = tf.sparse.SparseTensor.from_value(ArcNode)
        self.NodeGraph = tf.sparse.SparseTensor.from_value(NodeGraph)

    # -----------------------------------------------------------------------------------------------------------------
    def copy(self):
        return GraphTensor(nodes=self.nodes, arcs=self.arcs, targets=self.targets, set_mask=self.set_mask, output_mask=self.output_mask,
                           sample_weights=self.sample_weights, Adjacency=self.Adjacency, ArcNode=self.ArcNode, NodeGraph=self.NodeGraph,
                           aggregation_mode=self.aggregation_mode)

    ## REPRESENTATION METHODs #########################################################################################
    def __repr__(self):
        set_mask_type = 'all' if tf.reduce_all(self.set_mask) else 'mixed'
        return f"graph_tensor(n={self.nodes.shape[0]}, a={self.arcs.shape[0]}, " \
               f"ndim={self.DIM_NODE_LABEL}, adim={self.DIM_ARC_LABEL}, tdim={self.DIM_TARGET}, " \
               f"set={set_mask_type}, mode={self.aggregation_mode})"

    # -----------------------------------------------------------------------------------------------------------------
    def __str__(self):
        return self.__repr__()

    ## CLASS and STATHIC METHODs ######################################################################################
    @classmethod
    def fromGraphObject(self, g: GraphObject):
        """ Create GraphTensor from GraphObject. Note that Adjacency and ArcNode are transposed so that GraphTensor.ArcNode and
        GraphTensor.Adjacency are ready for sparse_dense_matmul in Loop operations.
        """
        return self(nodes=g.nodes, arcs=g.arcs, targets=g.targets, set_mask=g.set_mask, output_mask=g.output_mask,
                    sample_weights=g.sample_weights, NodeGraph=self.COO2SparseTensor(g.NodeGraph), Adjacency=self.COO2SparseTensor(g.Adjacency),
                    ArcNode=self.COO2SparseTensor(g.ArcNode), aggregation_mode=g.aggregation_mode)

    # -----------------------------------------------------------------------------------------------------------------
    @staticmethod
    def COO2SparseTensor(coo_matrix) -> tf.Tensor:
        """ Get the transposed sparse tensor from a sparse coo_matrix matrix """
        # SparseTensor is created and then reordered to be correctly computable. NOTE: reorder() recommended by TF2.0+

        if not all(coo_matrix.shape):
            indices = np.zeros(shape=(0, 2), dtype=int)
        else: indices = list(zip(coo_matrix.row, coo_matrix.col))
        sparse_tensor = tf.SparseTensor(indices, values=coo_matrix.data, dense_shape=coo_matrix.shape)
        sparse_tensor = tf.sparse.reorder(sparse_tensor)
        sparse_tensor = tf.cast(sparse_tensor, dtype=tf.keras.backend.floatx())
        return sparse_tensor