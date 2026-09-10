{
  description = "Factory video analytics pipeline (ingestion + GPU processing)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true; # needed for CUDA
        };

        pythonEnv = pkgs.python311.withPackages (ps: with ps; [
          pip
          pyyaml
          opencv4
          numpy
          pandas
          sqlalchemy
          ultralytics    # YOLO + built-in ByteTrack
          torch
          torchvision
          fastapi
          uvicorn
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            pkgs.ffmpeg-full     # or ffmpeg-full with nvenc/nvdec if built with cuda support
            pkgs.sqlite
          ];

          shellHook = ''
            export PROJECT_ROOT=$(pwd)
            export PYTHONPATH=$PROJECT_ROOT/scripts:$PYTHONPATH
            echo "Video pipeline dev shell ready."
            echo "GPU visible to torch? run: python -c 'import torch; print(torch.cuda.is_available())'"
          '';
        };
      });
}
