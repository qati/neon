# ----------------------------------------------------------------------------
# Copyright 2016 Nervana Systems Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ----------------------------------------------------------------------------
import os
import numpy as np
from zipfile import ZipFile
from glob import glob
from neon.util.persist import get_data_cache_or_nothing
from neon.data.aeon_shim import AeonDataLoader
from neon.data.dataloader_transformers import OneHot, TypeCast


def ingest_whales(input_dir, out_dir, train_frac=0.8):
    '''
    save_ingested_whale_files
    '''
    zfilename = os.path.join(input_dir, 'whale_data.zip')
    orig_out_dir = out_dir
    out_dir = os.path.join(orig_out_dir, 'whale-extracted')
    set_names = ['all', 'val', 'train', 'noise', 'test']
    manifests = {s: os.path.join(out_dir, s + '-index.csv') for s in set_names}

    manifest_list_cfg = ', '.join([k+':'+v for k, v in manifests.items()])

    cfg_path = os.path.dirname(os.path.realpath(__file__))
    for runtype in ('eval', 'subm'):
        cfg_file = os.path.join(cfg_path, 'whale_' + runtype + '.cfg')
        log_file = os.path.join(orig_out_dir, 'train_' + runtype + '.log')
        with open(cfg_file, 'w') as f:
            f.write('manifest = [{}]\n'.format(manifest_list_cfg))
            f.write('manifest_root = {}\n'.format(out_dir))
            f.write('log = {}\n'.format(log_file))
            f.write('epochs = 16\nrng_seed = 0\nverbose = True\n')
            if runtype == 'subm':
                f.write('save_path = {}\n'.format(os.path.join(orig_out_dir, 'model.p')))
                f.write('submission_file = {}\n'.format(os.path.join(orig_out_dir, 'subm.txt')))

    if all([os.path.exists(manifests[sn]) for sn in set_names]):
        return [manifests[sn] for sn in set_names]

    with ZipFile(zfilename, 'r') as zf:
        zf.extractall(out_dir)

        # create label files
        lbl_files = [os.path.join('data', lbl + '.txt') for lbl in ('neg', 'pos')]

        np.savetxt(os.path.join(out_dir, lbl_files[0]), [0], fmt='%d')
        np.savetxt(os.path.join(out_dir, lbl_files[1]), [1], fmt='%d')

        input_csv = os.path.join(out_dir, 'data', 'train.csv')
        train_records = np.genfromtxt(input_csv, delimiter=',', skip_header=1, dtype=None)

        pos_list, neg_list = [], []

        for aiff, lbl in train_records:
            record = (os.path.join('data', 'train', aiff), lbl_files[lbl])
            if lbl == 1:
                pos_list.append(record)
            else:
                neg_list.append(record)

        neg_part, pos_part = int(len(neg_list) * train_frac), int(len(pos_list) * train_frac)

        set_lists = dict()
        set_lists['all'] = neg_list + pos_list
        set_lists['train'] = neg_list[:neg_part] + pos_list[:pos_part]
        set_lists['val'] = neg_list[neg_part:] + pos_list[pos_part:]

        # Use just the files from the non-whale calls to use as a set of noise samples
        set_lists['noise'] = [(a) for a, l in neg_list[:neg_part]]

        # Write out the test files
        set_lists['test'] = [os.path.relpath(fn, out_dir) for fn in glob(
                                                os.path.join(out_dir, 'data', 'test', '*.aiff'))]

        np.random.seed(0)

        for sn in set_names:
            if sn != 'test':
                np.random.shuffle(set_lists[sn])

            format_str = '%s' if sn in ('noise', 'test') else '%s,%s'
            np.savetxt(manifests[sn], set_lists[sn], fmt=format_str)

    return [manifests[sn] for sn in set_names]


def wrap_dataloader(dl):
    dl = OneHot(dl, index=1, nclasses=2)
    dl = TypeCast(dl, index=0, dtype=np.float32)
    return dl


def common_config(manifest_file, manifest_root, batch_size):
    cache_root = get_data_cache_or_nothing('whale-cache/')

    return {
               'manifest_filename': manifest_file,
               'manifest_root': manifest_root,
               'minibatch_size': batch_size,
               'macrobatch_size': batch_size * 12,
               'type': 'audio,label',
               'cache_directory': cache_root,
               'audio': {'sample_freq_hz': 2000,
                         'max_duration': '2 seconds',
                         'frame_length': '80 milliseconds',
                         'frame_stride': '40 milliseconds'},
               'label': {'binary': False}
            }


def make_val_loader(manifest_file, manifest_root, backend_obj):
    aeon_config = common_config(manifest_file, manifest_root, backend_obj.bsz)
    return wrap_dataloader(AeonDataLoader(aeon_config, backend_obj))


def make_train_loader(manifest_file, manifest_root, backend_obj, noise_file=None, random_seed=0):
    aeon_config = common_config(manifest_file, manifest_root, backend_obj.bsz)
    aeon_config['shuffle_manifest'] = True
    aeon_config['shuffle_every_epoch'] = True
    aeon_config['random_seed'] = random_seed

    if noise_file is not None:
        aeon_config['audio']['noise_index_file'] = noise_file
        aeon_config['audio']['noise_root'] = manifest_root
        aeon_config['audio']['add_noise_probability'] = 0.5
        aeon_config['audio']['noise_level'] = [0.0, 0.5]

    return wrap_dataloader(AeonDataLoader(aeon_config, backend_obj))


def make_test_loader(manifest_file, manifest_root, backend_obj):
    aeon_config = common_config(manifest_file, manifest_root, backend_obj.bsz)
    aeon_config['type'] = 'audio'  # No labels provided
    aeon_config.pop('label', None)
    dl = AeonDataLoader(aeon_config, backend_obj)
    dl = TypeCast(dl, index=0, dtype=np.float32)
    return dl


if __name__ == '__main__':
    from configargparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument('--input_dir', required=True, help='path to whale_data.zip')
    parser.add_argument('--out_dir', required=True, help='destination path of extracted files')
    args = parser.parse_args()

    generated_files = ingest_whales(args.input_dir, args.out_dir)

    print("Manifest files written to:\n" + "\n".join(generated_files))
