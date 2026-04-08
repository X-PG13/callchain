struct Calculator {
    value: i32,
}

impl Calculator {
    fn new(value: i32) -> Self {
        Calculator { value }
    }

    fn add(&mut self, x: i32) -> i32 {
        self.value = increment(self.value, x);
        self.value
    }
}

fn increment(a: i32, b: i32) -> i32 {
    a + b
}

fn main() {
    let mut calc = Calculator::new(10);
    let result = calc.add(5);
    println!("{}", result);
}
