import ydb.apps.dstool.lib.common as common
import ydb.apps.dstool.lib.table as table
import re
import multiprocessing
import sys
from collections import defaultdict

description = 'Estimate storage efficiency of VDisks and pools'


def create_table_output():
    size_cols = ['IdxSize', 'InplaceSize', 'HugeSize', 'CompIdxSize', 'CompInplaceSize', 'CompHugeSize', 'Size', 'CompSize']
    columns = ['StoragePool', 'GroupId', 'Blobs', 'CompBlobs', *size_cols, 'Efficiency']

    def aggr_size(d, rg):
        for row in rg:
            for key_col in size_cols + ['Blobs', 'CompBlobs']:
                d[key_col] = d.get(key_col, 0) + row[key_col]
        if d['Size']:
            d['Efficiency'] = d['CompSize'] / d['Size']
        return d

    aggregations = {
        'group': (['GroupId'], aggr_size),
    }
    return table.TableOutput(
        cols_order=columns,
        aggregations=aggregations,
        aggr_drop={'Blobs', 'CompBlobs', *size_cols, 'Efficiency'},
        col_units=dict([(x, 'bytes') for x in size_cols] + [('Efficiency', '%')])
    )


table_output = create_table_output()


def parse_vdisk_storage_efficiency(host, node_id, pdisk_id, vslot_id):
    idx_size, inplace_size, huge_size, comp_idx_size, comp_inplace_size, comp_huge_size = 0, 0, 0, 0, 0, 0
    items, comp_items = 0, 0
    page = 'actors/vdisks/vdisk%09u_%09u' % (pdisk_id, vslot_id)
    size_col = 'Idx / Inplaced / Huge Size'
    items_col = 'Items / WInplData / WHugeData'
    usage_col = 'Idx% / IdxB% / InplB% / HugeB%'
    count_items = True
    try:
        data = common.fetch(page, {}, host, fmt='raw').decode('utf-8')
        for t in re.finditer(r'<thead><tr>(.*?)</tr></thead><tbody>(.*?)</tbody>', data, re.S):
            cols = [m.group(1) for m in re.finditer('<th>(.*?)</th>', t.group(1))]
            if size_col not in cols or usage_col not in cols or items_col not in cols:
                continue
            for row in re.finditer(r'<tr>(.*?)</tr>', t.group(2), re.S):
                cells = dict(zip(cols, re.findall(r'<td>(.*?)</td>', row.group(1), re.S)))
                sizes = list(map(int, re.fullmatch(r'<small>(.*)</small>', cells[size_col]).group(1).split(' / ')))
                items_r = list(map(int, re.fullmatch(r'<small>(.*)</small>', cells[items_col]).group(1).split(' / ')))
                usage = re.fullmatch(r'<small>(.*)</small>', cells[usage_col]).group(1).split(' / ')
                if usage == ['ratio']:
                    continue
                elif usage == ['UNK']:
                    usage = ['100', '100', '100', '100']
                if count_items:
                    items += items_r[0]
                    comp_items += int(items_r[0] * float(usage[0]) / 100)
                idx_size += sizes[0]
                inplace_size += sizes[1]
                huge_size += sizes[2]
                comp_idx_size += int(sizes[0] * float(usage[1]) / 100)
                if usage[2] != 'NA':
                    comp_inplace_size += int(sizes[1] * float(usage[2]) / 100)
                else:
                    assert sizes[1] == 0
                if usage[3] != 'NA':
                    comp_huge_size += int(sizes[2] * float(usage[3]) / 100)
                else:
                    assert sizes[2] == 0
            count_items = False
    except Exception as e:
        print('Failed to process VDisk %s: %s' % (page, e))
        return None
    return idx_size, inplace_size, huge_size, comp_idx_size, comp_inplace_size, comp_huge_size, items, comp_items


def add_options(p):
    table_output.add_options(p)


def do(args):
    base_config_and_storage_pools = common.fetch_base_config_and_storage_pools()
    base_config = base_config_and_storage_pools['BaseConfig']
    node_fqdn_map = common.build_node_fqdn_map(base_config)
    storage_pools = base_config_and_storage_pools['StoragePools']
    sp_map = common.build_storage_pool_names_map(storage_pools)

    group_to_sp_name = {
        g.GroupId: sp_map[g.BoxId, g.StoragePoolId]
        for g in base_config.Group
        if (g.BoxId, g.StoragePoolId) in sp_map
    }

    for group in base_config.Group:
        group_id = group.GroupId
        box_id = group.BoxId
        pool_id = group.StoragePoolId
        if (box_id, pool_id) not in sp_map:
            common.print_if_verbose(args, f"Can't find group {group_id} in box {box_id}, pool {pool_id}", sys.stderr)

    host_requests_map = defaultdict(list)
    group_size_map = {}
    for group in base_config.Group:
        group_size_map[group.GroupId] = len(group.VSlotId)
        for vslot in group.VSlotId:
            host = node_fqdn_map[vslot.NodeId]
            host_requests_map[host].append((group.GroupId, vslot.NodeId, vslot.PDiskId, vslot.VSlotId))

    def fetcher(host, items, res_q):
        for group_id, node_id, pdisk_id, vslot_id in items:
            row = parse_vdisk_storage_efficiency(host, node_id, pdisk_id, vslot_id)
            if row is not None:
                res_q.put((group_id, *row))
        res_q.put(None)

    processes = []
    res_q = multiprocessing.Queue()
    for key, value in host_requests_map.items():
        processes.append(multiprocessing.Process(target=fetcher, kwargs=dict(host=key, items=value, res_q=res_q), daemon=True))
    for p in processes:
        p.start()
    num_q = len(processes)
    results = defaultdict(list)
    found = defaultdict(int)
    while num_q:
        item = res_q.get()
        if item is None:
            num_q -= 1
            continue
        r = results[item[0]]
        if not r:
            r.extend(item[1:])
        else:
            for i, value in enumerate(item[1:]):
                r[i] += value
        found[item[0]] += 1
    for p in processes:
        p.join()

    rows = []
    for group_id, values in results.items():
        if found[group_id] != group_size_map[group_id]:
            for i in range(len(values)):
                values[i] = int(values[i] * group_size_map[group_id] / found[group_id])
        rows.append(dict(
            GroupId=group_id,
            StoragePool=group_to_sp_name.get(group_id, 'static'),
            IdxSize=values[0],
            InplaceSize=values[1],
            HugeSize=values[2],
            CompIdxSize=values[3],
            CompInplaceSize=values[4],
            CompHugeSize=values[5],
            Size=values[0] + values[1] + values[2],
            CompSize=values[3] + values[4] + values[5],
            Blobs=values[6],
            CompBlobs=values[7],
        ))
        if rows[-1]['Size']:
            rows[-1]['Efficiency'] = rows[-1]['CompSize'] / rows[-1]['Size']

    table_output.dump(rows, args)
