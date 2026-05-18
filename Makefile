.PHONY: build-qlcp build-protocol

build-qlcp:
	cmake -S qlcp -B qlcp/build -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release
	cmake --build qlcp/build --target qlcp
	mkdir -p libqretprop/_lib
	cp qlcp/build/libqlcp.so libqretprop/_lib/libqlcp.so
	gcc -E qlcp/include/qlcp_lib.h | \
		awk '/^# [0-9]+ "/{in_our_header=($$3!~/^"\/usr/&&$$3!~/^"</);next} in_our_header{print}' \
		> libqretprop/_lib/qlcp_lib_expanded.h
	test -s libqretprop/_lib/qlcp_lib_expanded.h || (echo "ERROR: expanded header is empty" && exit 1)

build-protocol: build-qlcp
	uv run python libqretprop/_build_qlcp.py