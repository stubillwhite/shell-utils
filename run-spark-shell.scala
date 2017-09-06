import com.amazonaws.auth.BasicAWSCredentials
import com.amazonaws.services.s3.AmazonS3Client
import com.typesafe.config.{Config, ConfigFactory}
import java.io._
import org.apache.spark.sql._
import org.apache.spark.sql.types._
import spark.implicits._

if (!(sys.env.contains("AWS_ACCESS_KEY_ID") && sys.env.contains("AWS_SECRET_ACCESS_KEY"))) {
  println("Please define both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to use S3. Continuing in local mode only.")
} else {
  println("Setting up S3 credentials.")
  
  val awsAccessKey = sys.env("AWS_ACCESS_KEY_ID")
  val awsSecretKey = sys.env("AWS_SECRET_ACCESS_KEY")

  val hadoopConfiguration = spark.sparkContext.hadoopConfiguration
  hadoopConfiguration.set("fs.s3.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
  hadoopConfiguration.set("fs.s3a.awsAccessKeyId", awsAccessKey)
  hadoopConfiguration.set("fs.s3a.awsSecretAccessKey", awsSecretKey)
}
