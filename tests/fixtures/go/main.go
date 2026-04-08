package main

import "fmt"

type Calculator struct {
	value int
}

func NewCalculator(value int) *Calculator {
	return &Calculator{value: value}
}

func (c *Calculator) Add(x int) int {
	c.value = Increment(c.value, x)
	return c.value
}

func Increment(a, b int) int {
	return a + b
}

func main() {
	calc := NewCalculator(10)
	result := calc.Add(5)
	fmt.Println(result)
}
