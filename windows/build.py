#!/usr/bin/env python3

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import json
import urllib.request
from zipfile import ZipFile

# Handle commandline arguments
parser = argparse.ArgumentParser()
parser.add_argument('--release', action='store_true')
parser.add_argument('--sign', action='store_true')
parser.add_argument('--cargo', default='cargo')
parser.add_argument('--wix', default="C:/Program Files (x86)/WiX Toolset v3.11")
args = parser.parse_args()

CARGO = shlex.split(args.cargo)
# Executables to install
TARGET_DIR = "../target/" + ('release' if args.release else 'debug')
EXES = {
    f"{TARGET_DIR}/system76-keyboard-configurator.exe",
}
ICON = "../data/icons/scalable/apps/com.system76.keyboardconfigurator.svg"

DLL_RE = r"(?<==> )(.*\\mingw32)\\bin\\(\S+.dll)"

ADWAITA_FILES = [
    'index.theme',
    'scalable/actions/open-menu-symbolic.svg',
    'scalable/ui/window-close-symbolic.svg',
    'scalable/ui/window-maximize-symbolic.svg',
    'scalable/ui/window-minimize-symbolic.svg',
    'scalable/ui/window-restore-symbolic.svg',
    'scalable/actions/edit-delete-symbolic.svg',
    'scalable/actions/go-previous-symbolic.svg',
    'scalable/actions/list-remove-symbolic.svg',
    'scalable/actions/list-add-symbolic.svg',
    'scalable/actions/edit-find-symbolic.svg',
]
ADWAITA_FILES = [f'share/icons/Adwaita/{i}' for i in ADWAITA_FILES]
ADDITIONAL_FILES = ['share/glib-2.0/schemas/org.gtk.Settings.FileChooser.gschema.xml', 'share/icons/hicolor/index.theme', 'lib/p11-kit', 'lib/gdk-pixbuf-2.0'] + ADWAITA_FILES

# Use ntldd to find the mingw dlls required by a .exe
def find_depends(exe):
    if not os.path.exists(exe):
        sys.exit(f"'{exe}' does not exist")
    output = subprocess.check_output(['ntldd.exe', '-R', exe], universal_newlines=True)
    dlls = set()
    mingw_dir = None
    for l in output.splitlines():
        m = re.search(DLL_RE, l, re.IGNORECASE)
        if m:
            dlls.add((m.group(0), m.group(2)))
            mingw_dir = m.group(1)
    return mingw_dir, dlls


# Build application with rustup
cmd = CARGO + ['build']
if args.release:
    cmd.append('--release')
subprocess.check_call(cmd)

# Generate set of all required dlls
dlls = set()
mingw_dir = None
for i in EXES:
    mingw_dir_new, dlls_new = find_depends(i)
    dlls = dlls.union(dlls_new)
    mingw_dir = mingw_dir or mingw_dir_new

# The svg module is loaded at runtime, so it's dependencies are also needed
dlls = dlls.union(find_depends(f"{mingw_dir}/lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.dll")[1])

def copy(srcdir, destdir, path):
    src = f"{srcdir}/{path}"
    dest = f"{destdir}/{path}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"Copy {src} -> {dest}")
    if os.path.isdir(src):
        shutil.copytree(src, dest)
    else:
        shutil.copy(src, dest)

def strip(srcdir, destdir, path):
    src = f"{srcdir}/{path}"
    dest = f"{destdir}/{path}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"Strip {src} -> {dest}")
    subprocess.check_call([f"strip.exe", '-o', dest, src])

# Copy executables and libraries
if os.path.exists('out'):
    shutil.rmtree('out')
for i in EXES:
    strip(os.path.dirname(i), 'out', os.path.basename(i))
for src, filename in dlls:
    copy(os.path.dirname(src), 'out', filename)

# This shouldn't be necessary
# https://github.com/pop-os/keyboard-configurator/issues/39
copy('../data/icons', 'out/share/icons/Adwaita', 'scalable')

# Copy additional data
for i in ADDITIONAL_FILES:
    copy(mingw_dir, 'out', i)
subprocess.check_call(["glib-compile-schemas", "out/share/glib-2.0/schemas"])

# Extract crate version from cargo
meta_str = subprocess.check_output(CARGO + ["metadata", "--format-version", "1", "--no-deps"])
meta = json.loads(meta_str)
package = next(i for i in meta['packages'] if i['name'] == 'system76-keyboard-configurator')
crate_version = package['version']

# Generate Icon and installer banner
subprocess.check_call(["rsvg-convert", "--width", "256", "--height", "256", "-o", "keyboard-configurator.png", ICON])
subprocess.check_call(["convert", "keyboard-configurator.png", "out/keyboard-configurator.ico"])
subprocess.check_call(["rsvg-convert", "--width", "493", "--height", "58", "-o", "banner.png", "banner.svg"])
subprocess.check_call(["convert", "banner.png", "banner.bmp"])
subprocess.check_call(["rsvg-convert", "--width", "493", "--height", "312", "-o", "dialog.png", "dialog.svg"])
subprocess.check_call(["convert", "dialog.png", "dialog.bmp"])

# Generate libraries.wxi
with open('libraries.wxi', 'w') as f:
    f.write("<!-- Generated by build.py -->\n")
    f.write('<Include>\n')

    def add_files(dirpath, indent):
        id_ = os.path.relpath(dirpath, 'out').replace('\\', '_').replace('/', '_').replace('-', '_').replace('.', '_')
        f.write(f"{indent}<Directory Id='{id_}' Name='{os.path.basename(dirpath)}'>\n")
        for i in os.scandir(dirpath):
            if i.is_dir():
                add_files(i.path, indent + ' ' * 4)
            else:
                id_ = i.path.replace('\\', '_').replace('-', '_').replace('.', '_')
                f.write(f"{indent}<Component Feature='Complete' Guid='*'>\n")
                f.write(f"{indent}    <File Id='{id_}' Name='{i.name}' Source='{i.path}' />\n")
                f.write(f"{indent}</Component>\n")
        f.write(f"{indent}</Directory>\n")

    for i in ['lib', 'share']:
        add_files(f"out\\{i}", ' ' * 4)

    for _, i in dlls:
        f.write(f"    <Component Feature='Complete' Guid='*'>\n")
        f.write(f"        <File Name='{i}' Source='out/{i}' />\n")
        f.write(f"    </Component>\n")

    f.write('</Include>\n')

# Build .msi
subprocess.check_call([f"{args.wix}/bin/candle.exe", ".\keyboard-configurator.wxs", f"-dcrate_version={crate_version}"])
subprocess.check_call([f"{args.wix}/bin/light.exe", "-ext", "WixUIExtension", ".\keyboard-configurator.wixobj"])

if args.sign:
    if not os.path.isdir('sign'):
        os.mkdir("sign")

    # Download signing tool
    tool_url = "https://www.ssl.com/download/codesigntool-for-windows"
    tool_zip = "sign/CodeSignTool.zip"
    if not os.path.isfile(tool_zip):
        if os.path.isfile(tool_zip + ".partial"):
            os.remove(tool_zip + ".partial")
        urllib.request.urlretrieve(tool_url, tool_zip + ".partial")
        os.rename(tool_zip + ".partial", tool_zip)

    # Extract signing tool
    tool_dir = "sign/CodeSignTool"
    if not os.path.isdir(tool_dir):
        if os.path.isdir(tool_dir + ".partial"):
            shutil.rmtree(tool_dir + ".partial")
        os.mkdir(tool_dir + ".partial")
        with ZipFile(tool_zip, "r") as zip:
            zip.extractall(tool_dir + ".partial")
        os.rename(tool_dir + ".partial", tool_dir)

    # Sign with specified cloud signing key
    subprocess.check_call([
        "cmd", "/c", "CodeSignTool.bat",
        "sign",
        "-credential_id=" + os.environ["SSL_COM_CREDENTIAL_ID"],
        "-username=" + os.environ["SSL_COM_USERNAME"],
        "-password=" + os.environ["SSL_COM_PASSWORD"],
        "-totp_secret=" + os.environ["SSL_COM_TOTP_SECRET"],
        "-program_name=System76 Keyboard Configurator",
        "-input_file_path=../../keyboard-configurator.msi",
        "-output_dir_path=../",
    ], cwd="sign/CodeSignTool")

    # Update MSI
    os.remove("keyboard-configurator.msi")
    os.rename("sign/keyboard-configurator.msi", "keyboard-configurator.msi")
