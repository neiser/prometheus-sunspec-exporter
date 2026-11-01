{ lib, python3Packages }:

python3Packages.buildPythonApplication {
  pname = "prometheus-sunspec-exporter";
  version = lib.strings.trim (builtins.readFile ../VERSION);
  pyproject = false;

  src = ../sunspec_exporter;

  dependencies = with python3Packages; [
    docopt
    prometheus-client
    (callPackage ./pysunspec.nix { })
  ];

  installPhase = ''
    runHook preInstall
    install -Dm755 sunspec_exporter.py $out/bin/prometheus-sunspec-exporter
    runHook postInstall
  '';

  meta = {
    description = "Prometheus exporter for SunSpec Modbus TCP devices";
    homepage = "https://github.com/neiser/prometheus-sunspec-exporter";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "prometheus-sunspec-exporter";
  };
}
