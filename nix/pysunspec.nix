{ lib, buildPythonPackage, fetchPypi, setuptools, pyserial }:

buildPythonPackage rec {
  pname = "pysunspec";
  version = "2.1.1";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-mCTIFamJNTmQKSdoN/6x89V0lkZBWRAxcCbTt/jZoUI=";
  };

  build-system = [ setuptools ];
  dependencies = [ pyserial ];

  pythonImportsCheck = [ "sunspec.core.client" ];

  meta = {
    description = "Python SunSpec tools";
    homepage = "https://github.com/sunspec/pysunspec";
    license = lib.licenses.mit;
  };
}
