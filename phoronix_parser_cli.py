#!/usr/bin/env python3
import argparse
import sys
from phoronix_parser import installer_map, phoronix_init, phoronix_list, phoronix_install


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Convert phoronix benchmarks into OpenForBC ones with ease.')
    parser.add_argument('mode', choices=['list', 'install'], help="Select working mode")
    try:
        selected_mode = sys.argv[1]
        if selected_mode == 'list':
            parser.add_argument('benchmark_name', nargs='?', help="Benchmark name")
            parser.add_argument('-p', '--platform', nargs='?', choices=installer_map.keys(), help="Select working mode")
            args = parser.parse_args()
            phoronix_init()
            phoronix_list(benchmark_name=args.benchmark_name, plat=args.platform)
        elif selected_mode == 'install':
            parser.add_argument('benchmark_name', help="Benchmark name")
            parser.add_argument('benchmark_version', nargs='?', help="Benchmark name")
            args = parser.parse_args()
            phoronix_init()
            phoronix_install(benchmark_name=args.benchmark_name, benchmark_v=args.benchmark_version)
        else:
            raise Exception("Unsupported mode")
    except Exception as e:
        print(e)
        error_state = True
        parser.print_usage()
