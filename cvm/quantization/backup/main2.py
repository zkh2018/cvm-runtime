import sys
from os import path
import configparser
import logging
import numpy as np

import mxnet as mx
from mxnet import gluon, ndarray as nd

from transformer import Model, reduce_graph, MRT
from gluon_zoo import save_model
import dataset as ds
import sim_quant_helper as sim
import utils
import sym_utils as sutils
import cvm_op

def set_batch(input_shape, batch):
    return [batch if s == -1 else s for s in input_shape]

def batch_axis(input_shape):
    idx = [i for i, s in enumerate(input_shape) if s == -1]
    assert len(idx) == 1
    return idx[0]

def _check(expression, section, option, message='Not a valid value'):
    assert expression, message + '.\noption `%s` in section `%s`' \
        % (option, section)

NoneType = object()

def _get_path(config, section, option, is_dir=False, dpath=NoneType):
    pth_ = _get_val(config, section, option, dval=dpath)
    pth = path.abspath(path.expanduser(pth_))
    if is_dir:
        _check(path.isdir(pth), section, option,
               message='Not a valid dir `%s`' % pth_)
        if not path.exists(pth):
            path.makedirs(pth)
    else:
        _check(path.exists(pth), section, option,
               message='File `%s` not found' % pth_)
    return pth

def _get_ctx(config, section, dctx=mx.cpu()):
    contex = dctx
    device_type = _get_val(config, section, 'Device_type', dval='cpu')
    _check(device_type in ['', 'gpu', 'cpu'], section, 'Device_type',
           message='Only support `gpu`, `cpu` and null value')
    if device_type == 'gpu':
        device_ids = _get_val(
            config, section, 'Device_ids', dtype='[int]')
        contex = mx.gpu(device_ids[0]) if len(device_ids) == 1 \
              else [mx.gpu(i) for i in device_ids]
        if section == 'CALIBRATION':
            _check(type(contex).__name__ != 'list', section, 'Device_ids',
                   message='`Device_ids` should be an integer in Calibration')
    else:
        device_ids = _get_val(config, section, 'Device_ids', dval='')
        _check(device_ids == '', section, 'Device_ids',
               message='`Device_ids` should be null given `cpu` device type')
    return contex

def _get_val(config, section, option, dtype='str', dval=NoneType):
    """ TODO(ryt): You'd better seperate the dtype format from the
                   embeded source code for flexiblity.

        Some Example Suggested:
            1. declare some basic data types:
                int_t, bool_t, str_t, etc.

            2. abstract the high-level structures in construction.
                using the ARRAY(int_t) function to indicate the
                custom defined structure of int array.
                others like PAIR, ARRAY, etc.

        We can then make a clear exposition into user for different
            key=value pairs, since the main2 documentation will be
            improving soon.
    """
    val_ = config[section][option]
    if val_ == '':
        _check(dval != NoneType, section, option,
               message="Please specify the value")
        val = dval
    elif dtype in ['str', 'int', 'tuple', 'float', 'bool']:
        val = _cast_val(section, option, val_, dtype=dtype)
    elif dtype.startswith('['):
        etype=dtype.replace('[', '').replace(']', '')
        val = [_cast_val(section, option, x.strip(), dtype=etype) \
               for x in val_.split(',')]
    elif dtype.startswith('{'):
        etypes = dtype.replace('{', '').replace('}', '').split(':')
        items = val_.split(',')
        val = {}
        for item in items:
            entries_ = [it.strip() for it in item.split(':')]
            _check(len(entries_) == len(etypes), section, option,
                   message="Dict level not consistent")
            entries = [_cast_val(section, option, entry_, dtype=etypes[level]) \
                for level, entry_ in enumerate(entries_)]
            cur = val
            for level, entry in enumerate(entries[:-2]):
                cur[entry] = {} if entry not in cur else cur[entry]
                cur = cur[entry]
            _check(entries[-2] not in cur, section, option,
                   message="Duplicate key `%s`" % entries[:-1])
            cur[entries[-2]] = entries[-1]
    return val

def _cast_val(section, option, val_, dtype='str'):
    if dtype == 'str':
        val = val_
    elif dtype in ['int', 'tuple', 'float', 'bool']:
        try:
            val = float(eval(val_)) if dtype == 'float' else eval(val_)
        except SyntaxError:
            print("Not a valid value, " + \
                  "option `%s` in section `%s`" % (option, section))
            sys.exit(0)
        if dtype == 'int':
            _check(type(val).__name__ == dtype, section, option,
                   message="Only support integer value")
    return val

def _load_fname(prefix, suffix=None, with_ext=False):
    suffix = "."+suffix if suffix is not None else ""
    return utils.extend_fname(prefix+suffix, with_ext)

def _checkpoint_exist(sec, *flist):
    for fname in flist:
        _check(path.exists(fname), 'DEFAULT', 'Start',
               message="Check point of `%s` not found, " % sec + \
               "please move the start point earlier")

if __name__ == "__main__":
    assert len(sys.argv) == 2, "Please enter 2 python arguments."
    cfgPath = sys.argv[1]
    baseDir = path.abspath(path.dirname(cfgPath))
    fileName = path.basename(cfgPath)
    absCfgPath = path.join(baseDir, fileName)

    cfg = configparser.ConfigParser()
    cfg.read(absCfgPath)

    # default
    sec = 'DEFAULT'
    verbosity = _get_val(cfg, sec, 'Verbosity',
                         dtype='int', dval=logging.NOTSET)
    utils.log_init(level=verbosity)
    logger = logging.getLogger("log.main")
    default_dir = path.expanduser("~/tvm-cvm/data")
    assert path.exists(default_dir), \
        "Please create the folder `data` in the working directory first"
    model_dir = _get_val(cfg, sec, 'Model_dir', dval=default_dir)
    model_name = _get_val(cfg, sec, 'Model_name')
    model_prefix = path.join(model_dir, model_name)
    model_ctx = _get_ctx(cfg, sec)
    input_shape = _get_val(cfg, sec, 'Input_shape', dtype='tuple')
    start_pos = {'DEFAULT': 0, 'PREPARE': 1, 'SPLIT_MODEL': 2, \
                 'CALIBRATION': 3, 'QUANTIZATION': 4, \
                 'MERGE_MODEL': 5}
    start = _get_val(cfg, sec, 'Start', dtype='str', dval='DEFAULT')
    _check(start in start_pos.keys(), sec, 'Start',
           message="Please choose a value from `%s`" % start_pos.keys())
    start_point = start_pos[start]

    # prepare
    sec = 'PREPARE'
    sym_file, prm_file = _load_fname(model_prefix, suffix='prepare')
    sym_path, prm_path = _load_fname(model_prefix)
    if not path.exists(sym_path) or not path.exists(prm_path):
        save_model(model_name, sym_path=sym_path, prm_path=prm_path)

    if start_point < 1:
        model = Model.load(sym_path, prm_path)
        model.prepare(set_batch(input_shape, 1))
        dump = _get_val(cfg, sec, 'Dump', dtype='bool', dval=False)
        if dump:
            model.save(sym_file, prm_file)
        logger.info("`%s` stage finihed" % sec)
    elif start_point == 1:
        _check(path.exists(sym_file) and path.exists(prm_file), 'DEFAULT',
               'Start', message="Check point of `%s` not found, " % sec + \
               "please move the start point earlier")
        model = Model.load(sym_file, prm_file)
        logger.info("`%s` stage checked" % sec)

    # split model
    sec = 'SPLIT_MODEL'
    keys = _get_val(cfg, sec, 'Keys', dtype='[str]', dval='')
    sym_top_file, prm_top_file = _load_fname(model_prefix, suffix='top')
    sym_base_file, prm_base_file = _load_fname(model_prefix, suffix='base')
    if keys == '':
        _check(start_point != 2, 'DEFAULT', 'Start',
               message="Invalid start point.")
        logger.info("`%s` stage skipped" % sec)
    elif start_point < 2:
        base, top = model.split(keys)
        dump = _get_val(cfg, sec, 'Dump', dtype='bool', dval=False)
        if dump:
            top.save(sym_top_file, prm_top_file)
            base.save(sym_base_file, prm_base_file)
        logger.info("`%s` stage finished" % sec)
    elif start_point == 2:
        _checkpoint_exist(
            sec, *[sym_top_file, prm_top_file, sym_base_file, prm_base_file])
        top = Model.load(sym_top_file, prm_top_file)
        base = Model.load(sym_base_file, prm_base_file)
        logger.info("`%s` stage checked" % sec)

    # calibration
    sec = 'CALIBRATION'
    model_name_calib = model_name + '.mrt.calibrate'
    batch = _get_val(cfg, sec, 'Batch', dtype='int', dval=16)
    ds_name = _get_val(cfg, sec, 'dataset')
    if start_point < 3:
        mrt = model.get_mrt() if keys == '' else base.get_mrt()
        calibrate_num = _get_val(
            cfg, sec, 'Calibrate_num', dtype='int', dval=1)
        lambd = _get_val(cfg, sec, 'Lambda', dtype='float', dval=None)
        shp = set_batch(input_shape, batch)
        dataset = ds.DS_REG[ds_name](shp)
        data_iter_func = dataset.iter_func()
        ctx = _get_ctx(cfg, sec, dctx=model_ctx)
        for i in range(calibrate_num):
            data, _ = data_iter_func()
            mrt.set_data(data)
            mrt.calibrate(lambd=lambd, ctx=ctx)
        dump = _get_val(cfg, sec, 'Dump', dtype='bool', dval=False)
        if dump:
            mrt.save(model_name_calib, datadir=model_dir)
        logger.info("`%s` stage finished" % sec)
    elif start_point == 3:
        _checkpoint_exist(
            sec, *list(utils.extend_fname(
            model_prefix+".mrt.calibrate", with_ext=True)))
        mrt = MRT.load(model_name_calib, datadir=model_dir)
        if keys != "":
            _checkpoint_exist(sec, sym_top_file, prm_top_file)
            top = Model.load(sym_top_file, prm_top_file)
        logger.info("`%s` stage checkd" % sec)

    # quantization
    sec = 'QUANTIZATION'
    model_name_quant = model_name + '.mrt.quantize'
    if start_point < 4:
        restore_names = _get_val(
            cfg, sec, 'Restore_name', dtype='[str]', dval=[])
        restore_names = set(restore_names)
        if '_ALL_EXCEPT_' in restore_names:
            from tfm_base import _pass_manager
            from sym_utils import topo_sort
            from tfm_ops import disabled_restore_ops

            quantize_ops = [op_name for op_name in _pass_manager["quantize"] \
                            if op_name not in disabled_restore_ops]
            restore_names_new = []
            for sym in topo_sort(mrt.current_model.symbol):
                name, op_name = sym.attr('name'), sym.attr('op_name')
                if op_name in quantize_ops and \
                    name not in restore_names:
                    restore_names_new.append(name)
            restore_names = set(restore_names_new)
        for name in restore_names:
            mrt.set_restore(name)
        input_precision = _get_val(
            cfg, sec, 'Input_precision', dtype='int', dval=None)
        if input_precision is not None:
            mrt.set_input_prec(input_precision)
        output_precision = _get_val(
            cfg, sec, 'Output_precision', dtype='int', dval=None)
        if output_precision is not None:
            mrt.set_output_prec(output_precision)
        ctx = _get_ctx(cfg, sec, dctx=model_ctx)
        softmax_lambd = _get_val(
            cfg, sec, 'Softmax_lambd', dtype='float', dval=None)
        if softmax_lambd is not None:
            mrt.set_softmax_lambd(softmax_lambd)
        shift_bits = _get_val(
            cfg, sec, 'Shift_bits', dtype='int', dval=None)
        if shift_bits is not None:
            mrt.set_shift_bits(shift_bits)
        thresholds = _get_val(
            cfg, sec, 'Thresholds', dtype='{str:float}', dval=None)
        if thresholds is not None:
            for name, threshold in thresholds.items():
                mrt.set_threshold(name, threshold)
        mrt.quantize()
        inputs_ext = mrt.get_inputs_ext()
        dump = _get_val(cfg, sec, 'Dump', dtype='bool', dval=False)
        if dump:
            mrt.save(model_name_quant, datadir=model_dir)
        logger.info("`%s` stage finished" % sec)
    elif start_point == 4:
        _checkpoint_exist(
            sec, *list(utils.extend_fname(
            model_prefix+'.mrt.quantize', with_ext=True)))
        mrt = MRT.load(model_name_quant, datadir=model_dir)
        inputs_ext = mrt.get_inputs_ext()
        dump = _get_val(cfg, sec, 'Dump', dtype='bool', dval=False)
        if keys != "":
            _checkpoint_exist(sec, sym_top_file, prm_top_file)
            top = Model.load(sym_top_file, prm_top_file)
        logger.info("`%s` stage checkd" % sec)

    # merge_model
    sec = 'MERGE_MODEL'
    sym_all_file, prm_all_file, ext_all_file = _load_fname(
        model_prefix, suffix='all.quantize', with_ext=True)
    if keys == '':
        _check(start_point != 5, 'DEFAULT', 'Start',
               message="Invalid start point.")
        qmodel = mrt.current_model
        oscales = mrt.get_output_scales()
        logger.info("`%s` stage skipped" % sec)
    elif start_point < 5:
        qmodel = mrt.current_model
        mrt_oscales = mrt.get_output_scales()
        model_merger = Model.merger(qmodel, top, mrt.get_maps())
        attribute_deps = _get_val(
            cfg, sec, 'Attribute_deps', dtype='{str:str:str}')

        name_idx = {mrt.get_maps().get(
            s.attr("name"), s.attr("name")): i \
            for i, s in enumerate(qmodel.symbol)}
        def mergefunc(node, params, graph):
            name, op_name = node.attr('name'), node.attr('op_name')
            childs, attr = sutils.sym_iter(
                node.get_children()), node.list_attr()
            if op_name in attribute_deps:
                attr_deps = attribute_deps[op_name]
                for attr_name, v in attr_deps.items():
                    val = sutils.get_attr(attr, attr_name, 0)
                    attr[attr_name] = int(val*mrt_oscales[name_idx[v]])
                node = sutils.get_mxnet_op(op_name)(
                    *childs, **attr, name=name)
            return node

        qmodel = model_merger.merge(callback=mergefunc)
        oscale_maps = _get_val(
            cfg, sec, 'Oscale_maps', dtype='{str:str}')
        oscales = model_merger.get_output_scales(
            mrt_oscales, oscale_maps)
        inputs_ext = mrt.get_inputs_ext()
        dump = _get_val(cfg, sec, 'Dump', dtype='bool', dval=False)
        if dump:
            qmodel.save(sym_all_file, prm_all_file)
            infos = ['oscales: ', oscales,
                     'input_ext: ', inputs_ext,
                     'input shapes: ', input_shape]
            sim.save_ext(ext_all_file, *infos)
        logger.info("`%s` stage finished" % sec)
    else:
        _check(start_point == 5, 'DEFAULT', 'Start',
               message='Start_point invalid')
        qmodel = Model.load(sym_all_file, prm_all_file)
        _, oscales, _, inputs_ext, _, _ = sim.load_ext(ext_all_file)
        logger.info("`%s` stage checked" % sec)

    # evaluation
    sec = 'EVALUATION'
    if sec in cfg.sections():
        iter_num = _get_val(cfg, sec, 'Iter_num', dtype='int', dval=0)
        batch = _get_val(cfg, sec, 'Batch', dtype='int', dval=batch)
        ctx = _get_ctx(cfg, sec, dctx=model_ctx)
        if isinstance(ctx, mx.Context):
            ctx = [ctx]
        org_model = Model.load(sym_path, prm_path)
        graph = org_model.to_graph(ctx=ctx)
        dataset = ds.DS_REG[ds_name](set_batch(input_shape, batch))
        data_iter_func = dataset.iter_func()
        metric = dataset.metrics()

        baxis = batch_axis(input_shape)
        olen = len(org_model.symbol)
        def forward(net, data, ctx):
            """ Multiple xpu run support.
            """
            data = gluon.utils.split_and_load(
                data, ctx_list=ctx, batch_axis=baxis, even_split=False)
            outs = [net(d) for d in data]
            if olen == 1:
                outs = nd.concatenate(outs)
            else:
                outs = [nd.concatenate([outs[i][j] \
                    for i in range(len(outs))]) for j in range(olen)]
            return outs

        def evalfunc(data, label):
            outs = forward(graph, data, ctx=ctx)
            acc = dataset.validate(metric, outs, label)
            return acc

        ngpus = len(ctx)
        _check(
            not batch % ngpus, sec, 'Device_ids',
            'Batch must be divisible by the number of gpus')
        split_batch = batch//ngpus
        qmodel = reduce_graph(qmodel, {
            'data': set_batch(input_shape, split_batch)})
        qgraph = qmodel.to_graph(ctx=ctx)
        qmetric = dataset.metrics()

        def quantize(data, label):
            data = sim.load_real_data(data, 'data', inputs_ext)
            outs = forward(qgraph, data, ctx)
            outs = outs / oscales[0] if olen == 1 \
                else [(t / oscales[i]) for i, t in enumerate(outs)]
            acc = dataset.validate(qmetric, outs, label)
            return acc

        if iter_num > 0:
            logger.info("Validating...")
            # TODO: calculate the total sample,
            #   currently sum the shape[0] as the number just for simplify
            utils.multi_validate(evalfunc, data_iter_func, quantize,
                                 iter_num=iter_num,
                                 logger=logging.getLogger('mrt.validate'))
            logger.info("`%s` stage finished" % sec)

    # compilation
    sec = 'COMPILATION'
    if sec in cfg.sections():
        dump_dir = _get_path(
            cfg, sec, 'Dump_dir', is_dir=True, dpath=model_dir)
        batch = _get_val(cfg, sec, 'Batch', dtype='int', dval=batch)
        model_name_tfm = model_name + "_tfm"
        qmodel.to_cvm(model_name_tfm, datadir=dump_dir,
                      input_shape=set_batch(input_shape, batch))

        dataset = ds.DS_REG[ds_name](set_batch(input_shape, batch))
        dump_data, _ = dataset.iter_func()()
        dump_data = sim.load_real_data(
            dump_data.astype("float64"), 'data', mrt.get_inputs_ext())
        np.save(path.join(dump_dir, model_name_tfm, "data.npy"),
                dump_data.astype('int8').asnumpy())
        logger.info("`%s` stage finished" % sec)
