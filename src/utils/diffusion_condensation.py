import heapq
from typing import List, Tuple

import numpy as np
import pandas as pd
from CATCH import catch
from scipy import sparse
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize
from utils.metrics import dice_coeff


def pos_enc_sinusoid(shape: Tuple[int]) -> np.array:
    '''
    Positional encoding for 2D image.
    Encoding scheme: Sinusoidal.

    `shape`: [H, W, C].
    '''

    H, W, C = shape
    freq = C // 2

    pos_enc = np.zeros((H, W, C))
    multiplier = np.exp(np.arange(0, freq, 2) * -(np.log(1e5) / freq))

    pos_H = np.arange(H)[:, None]
    pos_W = np.arange(W)[:, None]

    pos_enc[:, :, :freq:2] = np.sin(pos_H * multiplier)[:, :, None].transpose(
        0, 2, 1).repeat(W, axis=1)
    pos_enc[:, :, 1:freq:2] = np.cos(pos_H * multiplier)[:, :, None].transpose(
        0, 2, 1).repeat(W, axis=1)
    pos_enc[:, :, freq::2] = np.sin(pos_W * multiplier)[:, :, None].transpose(
        2, 0, 1).repeat(H, axis=0)
    pos_enc[:, :,
            freq + 1::2] = np.cos(pos_W * multiplier)[:, :, None].transpose(
                2, 0, 1).repeat(H, axis=0)

    return pos_enc


def diffusion_condensation_simple(X_orig: np.array,
                                  height_width: Tuple[int] = (128, 128),
                                  pos_enc_gamma: float = 0.0,
                                  similarity_thr: float = 0.95,
                                  convergence_ratio: float = 1e-4,
                                  return_all_segs: bool = False) -> np.array:
    '''
    `X_orig` : [N, C] feature matrix,
        where N := number of feature vectors
              C := number of features
    `pos_enc_gamma`: weighting for positional encoding.
    `similarity_thr`: a threshold above which nodes are considered connected.
    `convergence_ratio`: if values in the affinitiy matrix A
                         are mostly (judged by this ratio) identical,
                         we consider the diffusion condensation to have converged.

    Returns the clusters (distinct connected components in the converged affinity matrix).
    `clusters`: [N,] non-negative integers.

    Math:
        `A`: affinity matrix. Here defined as a scaled version of cosine similarity.
        `D`: degree matrix.
        `P`: diffusion matrix. P := D^-1 A.
    '''
    N, C = X_orig.shape

    X = normalize(X_orig)
    if pos_enc_gamma > 0:
        pos_enc = pos_enc_sinusoid((*height_width, C))
        pos_enc = pos_enc.reshape((-1, C))
        X += pos_enc_gamma * normalize(pos_enc)

    A_prev = np.zeros((X.shape[0], X.shape[0]))

    if return_all_segs:
        all_segs = []
    else:
        all_segs = None

    converged = False
    while not converged:
        A = 1 / 2 + 1 / 2 * cosine_similarity(X)
        A[A < similarity_thr] = 0

        MAE = np.sum(np.abs(A - A_prev))
        A_prev = A

        D_inv = np.diag(1.0 / np.sum(A, axis=1))
        D_inv_sparse = sparse.csr_matrix(D_inv)
        A_sparse = sparse.csr_matrix(A)
        P_sparse = D_inv_sparse @ A_sparse

        num_connected, clusters = sparse.csgraph.connected_components(
            csgraph=A_sparse, directed=False, return_labels=True)

        X = P_sparse @ X

        if return_all_segs:
            seg = clusters.reshape(*height_width)
            all_segs.append(seg)

        if MAE < convergence_ratio * N**2:
            converged = True

    return clusters, all_segs


def diffusion_condensation(X_orig: np.array,
                           height_width: Tuple[int] = (128, 128),
                           pos_enc_gamma: float = 0.0,
                           num_workers: int = 1,
                           return_all: bool = False,
                           random_seed: int = 0) -> np.array:
    '''
    `X_orig` : [N, C] feature matrix,
        where N := number of feature vectors
              C := number of features
    `pos_enc_gamma`: weighting for positional encoding.

    Returns the clusters (distinct connected components in the converged affinity matrix).
    `clusters`: [N,] non-negative integers.
    '''

    N, C = X_orig.shape

    X = normalize(X_orig, axis=1)
    if pos_enc_gamma > 0:
        pos_enc = pos_enc_sinusoid((*height_width, C))
        pos_enc = pos_enc.reshape((-1, C))
        X += pos_enc_gamma * normalize(pos_enc)

    data = pd.DataFrame(X)

    catch_op = catch.CATCH(knn=30,
                           random_state=random_seed,
                           n_pca=50,
                           n_jobs=num_workers)
    catch_op.fit(data)
    levels = catch_op.transform()

    clusters = catch_op.NxTs[levels[0]]

    if return_all:
        return clusters, (catch_op, levels)
    else:
        return clusters


def cluster_indices_from_mask(
        labels: np.array,
        mask: np.array,
        top1_only: bool = False) -> Tuple[List[int], dict]:
    '''
    `labels` is a label map from unsupervised clustering.
    `mask` is the ground truth mask of binary segmentation.
    This function estimates the list of cluster indices that corresponds to the mask.
    The current implementation uses a greedy algorithm.
    '''

    all_cluster_indices = np.unique(labels)

    # Use a max heap to track the dice scores.
    # By default, heapq maintains a min heap.
    # Hence we negate the dice score to "mimic" a max heap.
    dice_heap = []

    # Single-cluster dice scores.
    for cluster_idx in all_cluster_indices:
        heapq.heappush(dice_heap,
                       (-dice_coeff(labels == cluster_idx, mask), cluster_idx))

    if top1_only:
        best_dice, best_cluster_idx = heapq.heappop(dice_heap)
        return best_cluster_idx

    else:
        # Combine the different clusters.
        # Assuming the cluster with highest dice is definitely in the foreground.
        # Then try to merge in other clusters to see if dice increases.
        best_dice, best_cluster_idx = heapq.heappop(dice_heap)
        best_dice = -best_dice
        best_cluster_indices = [best_cluster_idx]
        dice_map = {best_cluster_idx: best_dice}
        for _ in range(len(dice_heap)):
            curr_dice, cluster_idx = heapq.heappop(dice_heap)
            dice_map[cluster_idx] = -curr_dice
            cluster_idx_candidate = best_cluster_indices + [cluster_idx]
            label_candidate = np.logical_or.reduce(
                [labels == i for i in cluster_idx_candidate])
            dice_candidate = dice_coeff(label_candidate, mask)
            if dice_candidate > best_dice:
                best_dice = dice_candidate
                best_cluster_indices = cluster_idx_candidate

        return best_cluster_indices, dice_map


def most_persistent_structures(labels: np.array,
                               min_frame_ratio: float = 0.4,
                               min_area_ratio: float = 1 / 500) -> np.array:
    '''
    Given a set of B labels on the same image, with shape [B, H, W]
    Return a label with the most persistent structures, with shape [H, W]
    '''
    B, H, W = labels.shape
    filtered_labels = labels.copy()
    persistent_label = np.zeros((H, W), dtype=np.int16)

    # Assign persistence score to each label index.
    #
    # Use a max heap to track the persistence scores.
    # By default, heapq maintains a min heap, which is what we want.
    persistence_heap = []
    for label_idx in np.unique(filtered_labels):
        sum_dice, sum_area_ratio, num_frames = 0, 0, 0
        existent_frames = np.sum(filtered_labels == label_idx, axis=(1, 2)) > 0
        for i in range(B - 1):
            if existent_frames[i] and existent_frames[i + 1]:
                num_frames += 1
                sum_dice += dice_coeff(
                    filtered_labels[i, ...] == label_idx,
                    filtered_labels[i + 1, ...] == label_idx)
                sum_area_ratio += np.sum(
                    filtered_labels[i, ...] == label_idx) / (H * W)

        mean_dice = 0 if num_frames == 0 else sum_dice / num_frames
        mean_area_ratio = 0 if num_frames == 0 else sum_area_ratio / num_frames
        persistence_score = mean_dice

        if mean_area_ratio < min_area_ratio or num_frames < min_frame_ratio * B:
            filtered_labels[filtered_labels == label_idx] = 0
        else:
            heapq.heappush(persistence_heap, (persistence_score, label_idx))

    # Re-color the label map, with label index with higher persistence score taking priority.
    for _ in range(len(persistence_heap)):
        persistence_score, label_idx = heapq.heappop(persistence_heap)
        # Ignore the background index.
        if label_idx == 0:
            continue
        loc = np.sum(filtered_labels == label_idx, axis=0) > min_frame_ratio
        persistent_label[loc] = label_idx

    # Re-number as continuous non-neg integers.
    persistent_label = continuous_renumber(persistent_label)
    filtered_labels = continuous_renumber(filtered_labels)

    return persistent_label, filtered_labels


def continuous_renumber(label: np.array) -> np.array:
    '''
    Renumber the entries of a label map as continous non-negative integers.
    '''
    label_orig = label.copy()
    val_before = np.unique(label_orig)
    val_after = np.arange(len(val_before))
    for (a, b) in zip(val_before, val_after):
        label[label_orig == a] = b

    return label


def associate_frames(labels: np.array) -> np.array:
    ordered_labels = labels.copy()

    B, H, W = labels.shape

    # Find the best-matching label indices pairs between adjacent frames.
    # Update the next frame using the matching label indices from the previous frame.
    for image_idx in range(B - 1):
        label_prev = ordered_labels[image_idx, ...]
        label_next = ordered_labels[image_idx + 1, ...]

        label_vec_prev = np.array(
            [label_prev.reshape(H * W) == i for i in np.unique(label_prev)],
            dtype=np.int16)
        label_vec_next = np.array(
            [label_next.reshape(H * W) == i for i in np.unique(label_next)],
            dtype=np.int16)

        # Use matrix multiplication to get intersection matrix.
        intersection_matrix = np.matmul(label_vec_prev, label_vec_next.T)

        # Use matrix multiplication to get union matrix.
        union_matrix = H * W - np.matmul(1 - label_vec_prev,
                                         (1 - label_vec_next).T)

        iou_matrix = intersection_matrix / union_matrix

        for i, label_idx_next in enumerate(np.unique(label_next)):
            # loc: pixels corresponding to `label_idx_next` in the next frame.
            loc = ordered_labels[image_idx + 1, ...] == label_idx_next
            if np.sum(iou_matrix[..., i]) > 0:
                label_idx_prev = np.unique(label_prev)[np.argmax(
                    iou_matrix[..., i])]
                ordered_labels[image_idx + 1, loc] = label_idx_prev
            else:
                ordered_labels[image_idx + 1, loc] = 0

    return ordered_labels