.PHONY: build-qlcp build-protocol

build-qlcp:
	cmake -S qlcp -B qlcp/build -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release
	cmake --build qlcp/build --target qlcp
	mkdir -p libqretprop/_lib
	cp qlcp/build/libqlcp.so libqretprop/_lib/libqlcp.so

build-protocol: build-qlcp
	uv run python libqretprop/_build_qlcp.py