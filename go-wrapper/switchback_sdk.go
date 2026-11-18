package main

// The only file that names the go-sdk switchback API (SwitchbackAssign and
// SwitchbackPermutation, added with switchbacks). Go cannot look a package
// function up at run time, so against a go-sdk without them the wrapper does
// not build; the lane is pinned to a go-sdk that has them.

import "github.com/absmartly/go-sdk/sdk"

type switchbackSDK struct {
	assign      func(clusterUnit string, periodIndex int64, seed int64, armCount int) (int, error)
	permutation func(clusterUnit string, cycleIndex int64, seed int64, armCount int) ([]int, error)
}

var switchbackFunctions = &switchbackSDK{assign: sdk.SwitchbackAssign, permutation: sdk.SwitchbackPermutation}
