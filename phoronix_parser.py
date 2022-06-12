#!/usr/bin/env python3

from __future__ import annotations
import git
import os
import glob
from xml.dom import minidom
from shutil import copy2 as cp
from contextlib import contextmanager, suppress
import json
import fileinput


from phoronix_downloader import PACKAGES_JSON_FILENAME, PhoronixDownloadDefinition

REMOTE_BENCH_ROOT_PATH = os.path.join("ob-cache", "test-profiles", "pts")
file_dir = os.path.dirname(os.path.abspath(__file__))
clone_dir = os.path.join(file_dir, "phoronix-benchs")
bench_root_path = os.path.join(clone_dir, REMOTE_BENCH_ROOT_PATH)
install_dir = os.path.join(file_dir, "phoronix-converted")
benchmark_info_template = os.path.join(file_dir, "phoronix_benchmark.json.template")
setup_template = os.path.join(file_dir, "phoronix_setup.sh.template")

installer_map = {
    "linux": "install.sh",
    "linux2": "install.sh",
    "darwin": "install_macosx.sh",
    "windows": "install_windows.sh",
}
bench_dict = {}


@contextmanager
def pipe():
    r, w = os.pipe()
    yield r, w
    os.close(r)
    os.close(w)


def generate_dict():
    for bench in sorted(os.listdir(bench_root_path)):
        bench_name, bench_v = bench.rsplit("-", 1)

        if bench_name not in bench_dict:
            bench_dict[bench_name] = {}

        for p, installer_name in installer_map.items():
            if os.path.isfile(os.path.join(bench_root_path, bench, installer_name)):
                if "versions" not in bench_dict[bench_name]:
                    bench_dict[bench_name]["versions"] = {}

                if bench_v in bench_dict[bench_name]["versions"]:
                    bench_dict[bench_name]["versions"][bench_v].append(p)
                else:
                    bench_dict[bench_name]["versions"][bench_v] = [p]


def phoronix_init():
    """
    Function responsible of cloning and syncing the local clone of phoronix definitions.
    This function is idempotent.
    """
    if not os.path.isdir(clone_dir):
        os.mkdir(clone_dir)

    repo = git.Repo.init(clone_dir)

    try:
        repo.create_remote(
            "origin", "https://github.com/phoronix-test-suite/phoronix-test-suite"
        )
    except git.exc.GitCommandError:
        print("Origin already set up.")

    repo.config_writer().set_value("core", "sparsecheckout", "true").release()

    sparse_checkout_info_file_path = os.path.join(
        clone_dir, ".git", "info", "sparse-checkout"
    )

    if os.path.isfile(sparse_checkout_info_file_path):
        sparse_checkout_info_file = open(sparse_checkout_info_file_path, "w")
    else:
        sparse_checkout_info_file = open(sparse_checkout_info_file_path, "x")
    sparse_checkout_info_file.write(REMOTE_BENCH_ROOT_PATH)
    sparse_checkout_info_file.close()

    rebase = False
    try:
        repo.git.reset("--hard", "origin/master")
        rebase = True
    except Exception:
        print("Nothing to reset")

    repo.remotes.origin.pull("master", rebase=rebase)


def phoronix_list(benchmark_name=None, plat=None):
    """
    Function capable of listing all the versions of a given benchmark.
    """
    from sys import platform

    if plat is None:
        plat = platform
    if benchmark_name is None or not benchmark_name:
        if not bench_dict:
            generate_dict()
        for bench_name, bench_data in bench_dict.items():
            for v, p in bench_data["versions"].items():
                if plat in p:
                    print(f"{bench_name} @ {v} [{plat}]")
    else:
        local_benchmark_repo = os.path.join(
            clone_dir, REMOTE_BENCH_ROOT_PATH, benchmark_name
        )
        results = glob.glob(os.path.join(bench_root_path, local_benchmark_repo + "*"))
        if results:
            if not bench_dict:
                generate_dict()
            for v, p in bench_dict[benchmark_name]["versions"].items():
                if plat in p:
                    print(f"{benchmark_name} @ {v} [{plat}]")
        else:
            raise Exception("Benchmark {} not found.".format(benchmark_name))
    pass


def phoronix_exists(benchmark_name, benchmark_v=None):
    """
    Function returning a boolean flag relative to existence of a given bench (with optional version).
    """
    if benchmark_name:
        if not bench_dict:
            generate_dict()
        if benchmark_name in bench_dict:
            if benchmark_v:
                return benchmark_v in bench_dict[benchmark_name]["versions"]
            else:
                return True
        else:
            raise Exception("Benchmark name not valid.")
    else:
        raise Exception("Please provide a non-empty benchmark name.")


def safe_mkdir(path):
    """
    An mkdir wrapper to avoid calling mkdir on existing directorires.
    """
    if not os.path.isdir(path):
        os.mkdir(path)


def file_inplace_replace(file_path, search_string, replace_string):
    """
    A function working very much as replace, but inplace on files.
    """
    with fileinput.FileInput(file_path, inplace=True) as file:
        for line in file:
            print(line.replace(search_string, replace_string), end="")


def convert_settings(settings_list, settings_dir):
    """
    Function which parses phoronix-defined CLI args converting them into json setting files.
    It iterates over a list of XML elements in the form (extracted from a test-definition.xml):

    <Entry>
        <Name>Fast</Name>
        <Value>-fast</Value>
    </Entry>
    <Entry>
        <Name>Medium</Name>
        <Value>-medium</Value>
    </Entry>
    <Entry>
        <Name>Thorough</Name>
        <Value>-thorough</Value>
    </Entry>
    <Entry>
        <Name>Exhaustive</Name>
        <Value>-exhaustive</Value>
    </Entry>

    """
    default_settings_file = ""
    save_default_settings = True

    safe_mkdir(settings_dir)
    if len(settings_list) == 0:
        name = "unique_preset"
        args = "no_setting_specified"
        dict = {"args": args}

        with open(os.path.join(settings_dir, f"preset-{name}.json"), "w+") as outfile:
            json.dump(dict, outfile)
            if save_default_settings:
                default_settings_file = f"preset-{name}.json"
                save_default_settings = False
    else:
        for setting in settings_list:
            if len(setting.getElementsByTagName("Name")) != 0:
                name = setting.getElementsByTagName("Name")[
                    0
                ].firstChild.nodeValue.lower()
            if len(setting.getElementsByTagName("Value")) != 0:
                args = setting.getElementsByTagName("Value")[0].firstChild.nodeValue

            dict = {"args": args}
            with open(
                os.path.join(settings_dir, f"preset-{name}.json"), "w+"
            ) as outfile:
                json.dump(dict, outfile)
                if save_default_settings:
                    default_settings_file = f"preset-{name}.json"
                    save_default_settings = False

    return default_settings_file


def install_installers(bench_path, target_dir):
    """
    A function which copies and chmod+x the installer scripts coming with phoronix benchmarks.
    Typical names are install.sh, install_macosx.sh, install_windows.sh .
    """
    from os.path import basename

    for installer in glob.glob(os.path.join(bench_path, "install*.sh")):
        cp(installer, target_dir)
        installer_path = os.path.join(target_dir, basename(installer))
        ensure_executable(installer_path)


def create_setup_file(target_dir, benchmark_name):
    """
    A function which creates a setup file from a template.
    """
    from os import chmod, stat
    from stat import S_IEXEC

    target_setup_file = os.path.join(target_dir, "setup.sh")
    cp(setup_template, target_setup_file)
    file_stat = stat(target_setup_file)
    chmod(target_setup_file, file_stat.st_mode | S_IEXEC)
    file_inplace_replace(
        file_path=target_setup_file,
        search_string="BENCHMARK_NAME",
        replace_string=benchmark_name,
    )


def create_info_file(
    target_dir,
    test_definition_xml,
    results_definition_xml,
    default_settings_file,
    benchmark_name,
):
    """
    A function which creates an OpenForBC benchmark by getting the info from the test-definition.xml file and
    by defining the run command, i.e. the script to be executed in order to run the benchmark.
    Title and Description are the sensitive tokens.
    Furthermore, this function takes informations from results-definition.xml about benchamrk results
    parsing in order to put them inside the benchmark.json file.
    """
    from sys import platform

    info_section = test_definition_xml.getElementsByTagName("TestInformation")[0]
    info_benchmark_name = info_section.getElementsByTagName("Title")[
        0
    ].firstChild.nodeValue
    info_benchmark_description = info_section.getElementsByTagName("Description")[
        0
    ].firstChild.nodeValue
    benchmark_run_command = "./" + benchmark_name

    target_benchmark_info_file = os.path.join(target_dir, "benchmark.json")
    cp(benchmark_info_template, target_benchmark_info_file)

    file_inplace_replace(
        file_path=target_benchmark_info_file,
        search_string="PUT_NAME_HERE",
        replace_string=info_benchmark_name,
    )
    file_inplace_replace(
        file_path=target_benchmark_info_file,
        search_string="PUT_DESCRIPTION_HERE",
        replace_string=info_benchmark_description,
    )
    file_inplace_replace(
        file_path=target_benchmark_info_file,
        search_string="PUT_DEFAULT_PRESETS_HERE",
        replace_string=default_settings_file,
    )
    file_inplace_replace(
        file_path=target_benchmark_info_file,
        search_string="PUT_RUN_COMMAND_HERE",
        replace_string=benchmark_run_command,
    )
    file_inplace_replace(
        target_benchmark_info_file,
        "@INSTALLER_FILENAME@",
        f"./{installer_map[platform]}",
    )

    # RESULTS PARSING
    # Results can be represented with two different tag inside the results-definition.xml,
    # <ResultParser> or <SystemMonitor>.

    regex = "(.*)"

    if len(results_definition_xml.getElementsByTagName("ResultsParser")) != 0:
        results_parser = results_definition_xml.getElementsByTagName("ResultsParser")
        results_dict = {}  # dict when I put all the statistics to parse
        for node in results_parser:
            stat = node.getElementsByTagName("OutputTemplate")[0].firstChild.nodeValue
            if len(node.getElementsByTagName("ArgumentsDescription")) != 0:
                stat_name = node.getElementsByTagName("ArgumentsDescription")[
                    0
                ].firstChild.nodeValue
            else:
                stat_name = "results"

            stat = stat.replace("#_RESULT_#", regex)
            mini_dict = {}
            mini_dict["regex"] = stat
            results_dict[stat_name] = mini_dict
            results_string = json.dumps(results_dict)
    else:
        results_parser = results_definition_xml.getElementsByTagName("SystemMonitor")
        results_dict = {}  # dict when I put all the statistics to parse
        for node in results_parser:
            stat = node.getElementsByTagName("Sensor")[0].firstChild.nodeValue
            stat_name = "results"

            stat = stat.replace("#_RESULT_#", regex)
            mini_dict = {}
            mini_dict["regex"] = stat
            results_dict[stat_name] = mini_dict
            results_string = json.dumps(results_dict)

    file_inplace_replace(
        file_path=target_benchmark_info_file,
        search_string="PUT_STATS_HERE",
        replace_string=results_string,
    )


def get_related_platform(xml_package):
    """
    Utility to get the platform name for a download descriptor.
    """
    related_platform = None

    if xml_package.getElementsByTagName("PlatformSpecific"):
        platform_string = xml_package.getElementsByTagName("PlatformSpecific")[
            0
        ].firstChild.nodeValue
        platform_string = platform_string.lower()

        if "linux" in platform_string:
            related_platform = "linux"
        elif "macos" in platform_string:
            related_platform = "darwin"
        elif "windows" in platform_string:
            related_platform = "windows"

    return related_platform


def get_download_packages(bench_path) -> list[PhoronixDownloadDefinition]:
    """
    A function which obtains the downloads info as described in the downloads.xml file.
    The output is either a dictionary or a json file.
    The downloads in the downloads.xml file are listed in the form:

    <PhoronixTestSuite>
        <Downloads>
            <Package>
            <URL>http://something.tar.xz</URL>
            <MD5>54202a002878e4d0877e6e84c54202a0</MD5>
            <SHA256>9810c8fd3afd35b4755c2a46f14fc66e2b9199c22e46a5946123c9250f2d1ccd</SHA256>
            <FileName>something.tar.xz</FileName>
            <FileSize>452155436</FileSize>
            <PlatformSpecific>Windows</PlatformSpecific>
            </Package>
        </Downloads>
    </PhoronixTestSuite>
    """
    from os.path import join

    downloads_xml_path = join(bench_path, "downloads.xml")

    downloads = []
    try:
        downloads_xml = minidom.parse(downloads_xml_path)
        packages_list = downloads_xml.getElementsByTagName("Package")

        for package in packages_list:
            urls = package.getElementsByTagName("URL")[0].firstChild.nodeValue.split(
                ","
            )
            filename = package.getElementsByTagName("FileName")[0].firstChild.nodeValue

            platform = get_related_platform(xml_package=package)

            md5 = None
            sha256 = None
            size = None
            with suppress(IndexError):
                md5 = package.getElementsByTagName("MD5")[0].firstChild.nodeValue
            with suppress(IndexError):
                sha256 = package.getElementsByTagName("SHA256")[0].firstChild.nodeValue
            with suppress(IndexError):
                size = int(
                    package.getElementsByTagName("FileSize")[0].firstChild.nodeValue
                )

            downloads.append(
                PhoronixDownloadDefinition(filename, platform, urls, size, md5, sha256)
            )

        return downloads
    except Exception:
        return []


def create_packages_file(bench_path: str, target_dir: str) -> None:
    from os.path import join

    PhoronixDownloadDefinition.into_json(
        get_download_packages(bench_path), join(target_dir, PACKAGES_JSON_FILENAME)
    )


def ensure_executable(path: str) -> None:
    from os import chmod, stat
    from stat import S_IEXEC

    chmod(path, stat(path).st_mode | S_IEXEC)


def phoronix_install(benchmark_name, benchmark_v=None):
    from os.path import dirname, join
    from shutil import copy

    if phoronix_exists(benchmark_name, benchmark_v):
        if not benchmark_v:
            benchmark_v = list(bench_dict[benchmark_name]["versions"].keys())[-1]
            print(
                f"Benchmark version not specified, defaulting to latest ({benchmark_v})"
            )
        else:
            print(f"Selected benchmark version: {benchmark_v}")
        bench_path = os.path.join(
            bench_root_path, "{}-{}".format(benchmark_name, benchmark_v)
        )

        test_definition_xml = minidom.parse(
            os.path.join(bench_path, "test-definition.xml")
        )
        results_definition_xml = minidom.parse(
            os.path.join(bench_path, "results-definition.xml")
        )
        settings_list = test_definition_xml.getElementsByTagName("Entry")

        safe_mkdir(install_dir)

        target_dir = os.path.join(
            install_dir, "phoronix-{}-{}".format(benchmark_name, benchmark_v)
        )

        safe_mkdir(target_dir)

        settings_dir = os.path.join(target_dir, "presets")
        default_settings_file = convert_settings(
            settings_list=settings_list, settings_dir=settings_dir
        )

        install_installers(bench_path=bench_path, target_dir=target_dir)

        create_setup_file(target_dir=target_dir, benchmark_name=benchmark_name)

        create_info_file(
            target_dir=target_dir,
            test_definition_xml=test_definition_xml,
            results_definition_xml=results_definition_xml,
            default_settings_file=default_settings_file,
            benchmark_name=benchmark_name,
        )

        copy(
            join(dirname(__file__), "phoronix_downloader.py"),
            join(target_dir, "phoronix_downloader.py"),
        )

        create_packages_file(bench_path, target_dir)
    else:
        raise Exception(
            f"The required benchmark {benchmark_name} @ {benchmark_v} doesn't exist."
        )


if __name__ == "__main__":
    phoronix_init()
    # phoronix_install("astcenc", "1.1.0")
    # phoronix_install("cinebench")
    # phoronix_list("cinebench")
    phoronix_list("astcenc")
    # phoronix_list()
