#!/usr/bin/env python3
"""
Python代码提取工具
提取项目中所有的类、函数和全局变量，并保存为JSON格式
"""

import ast
import json
import os
from pathlib import Path
from typing import List, Dict, Any


class CodeExtractor:
    """代码提取器类"""
    
    def __init__(self, project_path: str):
        """
        初始化代码提取器
        
        Args:
            project_path: 项目根目录路径
        """
        self.project_path = Path(project_path).resolve()
        self.results = {
            "classes": [],
            "functions": [],
            "variables": [],
            "decorators": [],
            "imports": []
        }
    
    def extract_signature(self, node: ast.FunctionDef) -> str:
        """
        提取函数/方法的签名
        
        Args:
            node: AST函数定义节点
            
        Returns:
            函数签名字符串
        """
        args = []
        
        # 处理普通参数
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)
        
        # 处理 *args
        if node.args.vararg:
            vararg_str = f"*{node.args.vararg.arg}"
            if node.args.vararg.annotation:
                vararg_str += f": {ast.unparse(node.args.vararg.annotation)}"
            args.append(vararg_str)
        
        # 处理 **kwargs
        if node.args.kwarg:
            kwarg_str = f"**{node.args.kwarg.arg}"
            if node.args.kwarg.annotation:
                kwarg_str += f": {ast.unparse(node.args.kwarg.annotation)}"
            args.append(kwarg_str)
        
        # 构建完整签名
        signature = f"{node.name}({', '.join(args)})"
        
        # 添加返回类型注解
        if node.returns:
            signature += f" -> {ast.unparse(node.returns)}"
        
        return signature
    
    def extract_decorators(self, node) -> List[Dict[str, Any]]:
        """
        提取装饰器信息
        
        Args:
            node: AST节点（函数或类）
            
        Returns:
            装饰器信息列表
        """
        decorators = []
        for decorator in node.decorator_list:
            decorator_info = {
                "raw": ast.unparse(decorator),
                "name": self._get_decorator_name(decorator),
                "arguments": self._get_decorator_arguments(decorator)
            }
            decorators.append(decorator_info)
        return decorators
    
    def _get_decorator_name(self, decorator) -> str:
        """
        获取装饰器名称
        
        Args:
            decorator: 装饰器AST节点
            
        Returns:
            装饰器名称
        """
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Attribute):
            return ast.unparse(decorator)
        elif isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Name):
                return decorator.func.id
            elif isinstance(decorator.func, ast.Attribute):
                return ast.unparse(decorator.func)
        return ast.unparse(decorator)
    
    def _get_decorator_arguments(self, decorator) -> List[str]:
        """
        获取装饰器的参数
        
        Args:
            decorator: 装饰器AST节点
            
        Returns:
            参数列表
        """
        if isinstance(decorator, ast.Call):
            args = []
            # 位置参数
            for arg in decorator.args:
                args.append(ast.unparse(arg))
            # 关键字参数
            for keyword in decorator.keywords:
                if keyword.arg:
                    args.append(f"{keyword.arg}={ast.unparse(keyword.value)}")
                else:
                    args.append(f"**{ast.unparse(keyword.value)}")
            return args
        return []
    
    def _record_decorator_definition(self, decorator, file_path: str):
        """
        记录装饰器的定义（如果在本项目中定义）
        
        Args:
            decorator: 装饰器AST节点
            file_path: 文件路径
        """
        # 这个方法用于标记哪些装饰器在使用，实际装饰器函数会在函数提取时自动捕获
        pass
    
    def extract_variable_definition(self, node: ast.Assign) -> str:
        """
        提取变量的定义方式
        
        Args:
            node: AST赋值节点
            
        Returns:
            变量定义字符串
        """
        try:
            return ast.unparse(node)
        except:
            return str(node.value)
    
    def get_relative_path(self, file_path: Path) -> str:
        """
        获取相对于项目根目录的路径
        
        Args:
            file_path: 文件绝对路径
            
        Returns:
            相对路径字符串
        """
        try:
            return str(file_path.relative_to(self.project_path))
        except ValueError:
            return str(file_path)
    
    def extract_from_file(self, file_path: Path):
        """
        从单个Python文件中提取信息
        
        Args:
            file_path: Python文件路径
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content, filename=str(file_path))
            relative_path = self.get_relative_path(file_path)
            
            self._extract_from_node(tree, relative_path)
            
        except SyntaxError as e:
            print(f"语法错误 in {file_path}: {e}")
        except Exception as e:
            print(f"处理文件出错 {file_path}: {e}")
    
    def _extract_from_node(self, node: ast.AST, file_path: str, parent_class: str = None):
        """
        递归提取AST节点中的信息
        
        Args:
            node: AST节点
            file_path: 文件相对路径
            parent_class: 父类名称（用于提取方法）
        """
        for child in ast.iter_child_nodes(node):
            # 提取类定义
            if isinstance(child, ast.ClassDef):
                class_info = {
                    "name": child.name,
                    "signature": self._get_class_signature(child),
                    "file_path": file_path,
                    "line_number": child.lineno,
                    "docstring": ast.get_docstring(child),
                    "decorators": self.extract_decorators(child),
                    "methods": [],
                    "bases": [ast.unparse(base) for base in child.bases]
                }
                
                # 提取类中的方法
                for item in child.body:
                    if isinstance(item, ast.FunctionDef):
                        method_info = {
                            "name": item.name,
                            "signature": self.extract_signature(item),
                            "decorators": self.extract_decorators(item),
                            "is_method": True,
                            "class_name": child.name,
                            "line_number": item.lineno,
                            "docstring": ast.get_docstring(item),
                            "is_async": isinstance(item, ast.AsyncFunctionDef)
                        }
                        class_info["methods"].append(method_info)
                
                self.results["classes"].append(class_info)
                
            # 提取函数定义（模块级别的函数）
            elif isinstance(child, ast.FunctionDef) and parent_class is None:
                function_info = {
                    "name": child.name,
                    "signature": self.extract_signature(child),
                    "file_path": file_path,
                    "line_number": child.lineno,
                    "docstring": ast.get_docstring(child),
                    "decorators": self.extract_decorators(child),
                    "is_async": isinstance(child, ast.AsyncFunctionDef)
                }
                self.results["functions"].append(function_info)
                
                # 如果装饰器是单独定义的装饰器函数，也记录它
                if child.decorator_list:
                    for decorator in child.decorator_list:
                        self._record_decorator_definition(decorator, file_path)
            
            # 提取全局变量（模块级别的赋值）
            elif isinstance(child, (ast.Assign, ast.AnnAssign)) and parent_class is None:
                self._extract_variables(child, file_path)

            # 提取导入语句
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                self.extract_imports(child, file_path)
    
    def _get_class_signature(self, node: ast.ClassDef) -> str:
        """
        获取类的签名（包括基类）
        
        Args:
            node: 类定义节点
            
        Returns:
            类签名字符串
        """
        if node.bases:
            bases = ', '.join([ast.unparse(base) for base in node.bases])
            return f"class {node.name}({bases})"
        return f"class {node.name}"
    
    def _extract_variables(self, node, file_path: str):
        """
        提取变量定义
        
        Args:
            node: 赋值节点
            file_path: 文件相对路径
        """
        try:
            if isinstance(node, ast.Assign):
                # 处理普通赋值 a = 1
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        var_info = {
                            "name": target.id,
                            "definition": ast.unparse(node),
                            "file_path": file_path,
                            "line_number": node.lineno,
                            "type_annotation": None
                        }
                        self.results["variables"].append(var_info)
            
            elif isinstance(node, ast.AnnAssign):
                # 处理带类型注解的赋值 a: int = 1
                if isinstance(node.target, ast.Name):
                    var_info = {
                        "name": node.target.id,
                        "definition": ast.unparse(node),
                        "file_path": file_path,
                        "line_number": node.lineno,
                        "type_annotation": ast.unparse(node.annotation) if node.annotation else None
                    }
                    self.results["variables"].append(var_info)
        except Exception as e:
            print(f"提取变量出错: {e}")

    def extract_imports(self, node, file_path: str):
        """
        提取导入语句

        Args:
            node: 导入节点 (ast.Import 或 ast.ImportFrom)
            file_path: 文件相对路径
        """
        try:
            if isinstance(node, ast.Import):
                # 处理 import module 或 import module as alias
                for alias in node.names:
                    import_info = {
                        "type": "import",
                        "module": alias.name,
                        "imported_names": [],
                        "alias": alias.asname,
                        "file_path": file_path,
                        "line_number": node.lineno
                    }
                    self.results["imports"].append(import_info)

            elif isinstance(node, ast.ImportFrom):
                # 处理 from module import name
                module = node.module if node.module else ""
                level = "." * node.level if node.level else ""
                full_module = level + module

                imported_names = []
                for alias in node.names:
                    if alias.name == "*":
                        imported_names.append({"name": "*", "alias": None})
                    else:
                        imported_names.append({
                            "name": alias.name,
                            "alias": alias.asname
                        })

                import_info = {
                    "type": "from_import",
                    "module": full_module,
                    "imported_names": imported_names,
                    "file_path": file_path,
                    "line_number": node.lineno
                }
                self.results["imports"].append(import_info)

        except Exception as e:
            print(f"提取导入出错: {e}")

    def extract_from_project(self, exclude_dirs: List[str] = None):
        """
        从整个项目中提取信息
        
        Args:
            exclude_dirs: 要排除的目录列表（如 ['venv', '__pycache__', '.git']）
        """
        if exclude_dirs is None:
            exclude_dirs = ['venv', '.venv', 'env', '__pycache__', '.git', 'node_modules', 'build', 'dist']
        
        print(f"开始扫描项目: {self.project_path}")
        
        python_files = []
        for root, dirs, files in os.walk(self.project_path):
            # 排除指定目录
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            
            for file in files:
                if file.endswith('.py'):
                    python_files.append(Path(root) / file)
        
        print(f"找到 {len(python_files)} 个Python文件")
        
        for i, file_path in enumerate(python_files, 1):
            print(f"处理 [{i}/{len(python_files)}]: {self.get_relative_path(file_path)}")
            self.extract_from_file(file_path)
        
        print(f"\n提取完成!")
        print(f"- 类: {len(self.results['classes'])} 个")
        print(f"- 函数: {len(self.results['functions'])} 个")
        print(f"- 变量: {len(self.results['variables'])} 个")
        print(f"- 导入: {len(self.results['imports'])} 个")
    
    def save_to_json(self, output_path: str = "code_structure.json"):
        """
        保存结果到JSON文件
        
        Args:
            output_path: 输出文件路径
        """
        output_file = Path(output_path)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        
        print(f"\n结果已保存到: {output_file.resolve()}")


def main():
    """主函数"""
    import sys
    
    # 获取项目路径
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
    else:
        project_path = input("请输入项目路径（默认为当前目录）: ").strip() or "."
    
    # 获取输出文件名
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    else:
        output_file = input("请输入输出JSON文件名（默认: code_structure.json）: ").strip() or "code_structure.json"
    
    # 创建提取器并执行
    extractor = CodeExtractor(project_path)
    extractor.extract_from_project()
    extractor.save_to_json(output_file)


if __name__ == "__main__":
    main()
