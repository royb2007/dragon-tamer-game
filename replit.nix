{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.websockets
    pkgs.python311Packages.pip
  ];
}
