from flask import Flask
from flask import render_template

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", aaa="这是一个测试")


if __name__ == "__main__":
    app.run()
