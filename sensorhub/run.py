"""Development entry point: python run.py"""
from sensorhub import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
